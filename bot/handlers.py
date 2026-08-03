import asyncio
import os
import logging
import time
from telegram import Update
from telegram.ext import ContextTypes
from core.scanner import read_qr_code
from bot.keyboards import get_approval_keyboard, get_bank_selection_keyboard
from bot.verification_flow import VerificationDecision, determine_verification_action
from database.session import SessionLocal
from core.matcher import process_incoming_slip
from core.names import split_bank_name
from services.easyslip import verify_slip, extract_amount_from_qr_payload, BANK_DROPDOWN_VALUES
from database.crud import add_audit_log, is_sheet_locked, SHEET_REOPEN_ACTION
from services.gsheets import append_to_sheet
import json
from database.models import Transaction, UsedQR
from telegram.error import TimedOut, NetworkError

logger = logging.getLogger(__name__)

# เวลารอรูปใบถัดไปของอัลบั้มเดียวกัน (Telegram ส่งมาติดๆ กันเป็นคนละ message)
MEDIA_GROUP_WAIT_SECONDS = 2.0
_media_groups: dict[str, dict] = {}
_media_group_lock = asyncio.Lock()


def normalize_bank_name(value: str) -> str:
    first_name, last_name_initial = split_bank_name(value)
    return f"{first_name}{last_name_initial}"


def names_match_in_bank_format(api_name: str, reported_name: str) -> bool:
    api_first, api_initial = split_bank_name(api_name)
    reported_first, reported_initial = split_bank_name(reported_name)

    if not api_first or not reported_first:
        return False
    if api_first != reported_first:
        return False

    # ถ้าฝั่งไหนไม่ได้ให้อักษรย่อนามสกุลมา (เช่น caption พิมพ์แค่ชื่อจริง) ให้เทียบแค่ชื่อจริงพอ
    if not api_initial or not reported_initial:
        return True
    return api_initial == reported_initial


def is_reported_name_verified(senders: list[str], reported_name: str) -> bool:
    """ข้อ 'ชื่อ' จะผ่านก็ต่อเมื่อเทียบกันได้จริงทั้งสองฝั่ง

    caption ไม่ได้พิมพ์ชื่อมา (format แบบ User) หรือ API อ่านชื่อผู้โอนไม่ได้
    ถือว่าไม่ผ่าน ต้องให้แอดมินกดยืนยันเอง
    """
    reported = (reported_name or "").strip()
    if not reported or reported == "-":
        return False

    verified_senders = [sender for sender in senders if sender and sender.strip()]
    if not verified_senders:
        return False

    return all(names_match_in_bank_format(sender, reported) for sender in verified_senders)


def unlock_after_failed_save(db, txn) -> None:
    """เขียนลงชีทไม่สำเร็จ ให้กลับไปรอแอดมินกดใหม่ได้ ไม่ค้างสถานะไว้เฉยๆ"""
    txn.status = "pending"
    add_audit_log(db, txn.batch_id, SHEET_REOPEN_ACTION)


def unique_receiver_account(receivers: list[str]) -> str:
    """ถ้าทุกสลิปในชุดเข้าบัญชีเดียวกัน คืนชื่อบัญชีนั้น ถ้าคนละบัญชีคืนค่าว่าง"""
    unique_accounts = {receiver.strip() for receiver in receivers if receiver and receiver.strip()}
    if len(unique_accounts) == 1:
        return unique_accounts.pop()
    return ""


def resolve_known_bank_value(txn) -> str:
    """บัญชีผู้รับที่ระบบรู้อยู่แล้วจากการอ่านสลิป

    คืนค่าเฉพาะกรณีที่ map เลข 4 หลักเข้ากับ ACCOUNT_MAPPING ได้จริง
    (ค่าที่ไม่ตรงกับตัวเลือกในชีท เช่น เลขบัญชีดิบหรือชื่อร้าน ให้ถือว่ายังไม่รู้ ต้องถามแอดมิน)
    """
    for value in [txn.receiver_account, txn.chat_bank]:
        cleaned = (value or "").strip()
        if cleaned in BANK_DROPDOWN_VALUES:
            return cleaned
    return ""


def build_bank_mismatch_reason(bank_codes: list[str], bank_matches: list[bool]) -> str:
    """อธิบายให้แอดมินเข้าใจว่าทำไมบัญชีผู้รับไม่ผ่าน (เลี่ยงศัพท์ในโค้ด)"""
    mismatched_codes = [code for code, matched in zip(bank_codes, bank_matches) if code and not matched]
    if mismatched_codes:
        return f"account ending in {', '.join(mismatched_codes)} is not a company account"
    if bank_codes:
        return "the receiver account is not a company account"
    return "could not read the receiver account from the slip"


def amounts_match(expected_amount: float | None, actual_amount: float | None) -> bool:
    if expected_amount is None or actual_amount is None:
        return False
    return round(float(expected_amount), 2) == round(float(actual_amount), 2)


def format_amount(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def format_thb(value: float | None) -> str:
    """ยอดเงินสำหรับแสดงในแชท ให้ติดหน่วยไปด้วยเสมอ"""
    if value is None:
        return "unknown"
    return f"{float(value):.2f} THB"


def format_check_line(label: str, passed: bool, pass_detail: str, fail_detail: str) -> str:
    """หนึ่งบรรทัดต่อหนึ่งเงื่อนไข ให้กวาดตาอ่านได้ว่าติดตรงไหน"""
    detail = pass_detail if passed else fail_detail
    return f"• {label}: {'PASS' if passed else 'FAIL'} — {detail}"


def describe_txn_amount(txn) -> str:
    """ยอดของรายการนี้ ใช้ยอดที่ตรวจจากสลิปก่อน ถ้าไม่มีค่อยใช้ยอดที่แจ้งในแชท"""
    return format_thb(txn.api_total_amount if txn.api_total_amount is not None else txn.chat_amount)


def summarize_qr_payload(qr_payload: str, keep: int = 18) -> str:
    cleaned = (qr_payload or "").strip()
    if len(cleaned) <= keep * 2:
        return cleaned
    return f"{cleaned[:keep]}...{cleaned[-keep:]}"


def sum_batch_amount(amounts: list[float | None]) -> tuple[float | None, bool]:
    """รวมยอดทุกใบในชุด คืน None ถ้าอ่านยอดไม่ได้สักใบ (ไม่ใช่ 0 เพราะจะทำให้เข้าใจผิด)"""
    known_amounts = [float(amount) for amount in amounts if amount is not None]
    missing = len(known_amounts) != len(amounts)
    if not known_amounts:
        return None, missing
    return sum(known_amounts), missing


def join_unique(values: list[str]) -> str:
    """รวมรายชื่อโดยตัดค่าซ้ำออก เช่น ['KKP-LS', 'KKP-LS'] -> 'KKP-LS'"""
    unique_values = []
    for value in values:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in unique_values:
            unique_values.append(cleaned)
    return ", ".join(unique_values)


def verify_slip_batch(db, qr_list: list[str], batch_id: str) -> dict:
    """ยิง API ตรวจสอบสลิปทุกใบในรูปเดียวกัน แล้วรวมผลลัพธ์เป็นก้อนเดียว"""
    result = {
        "api_success": True,
        "amounts": [],
        "senders": [],
        "receivers": [],
        "bank_codes": [],
        "bank_matches": [],
        "user_error_msg": "",
    }

    for qr in qr_list:
        logger.info(
            "Start verifying QR | batch_id=%s | payload=%s | payload_amount=%s",
            batch_id,
            summarize_qr_payload(qr),
            format_amount(extract_amount_from_qr_payload(qr)),
        )
        api_result = verify_slip(qr)

        if api_result["success"]:
            logger.info(
                "Verify success | batch_id=%s | amount=%s | sender=%s | receiver=%s | bank_code=%s | bank_matches=%s",
                batch_id,
                format_amount(api_result.get("amount")),
                api_result.get("sender", ""),
                api_result.get("receiver", ""),
                api_result.get("receiver_bank_code", ""),
                api_result.get("receiver_bank_matches", False),
            )
            result["amounts"].append(api_result["amount"])
            result["senders"].append(api_result["sender"])
            result["receivers"].append(api_result["receiver"])
            result["bank_codes"].append(api_result.get("receiver_bank_code", ""))
            result["bank_matches"].append(api_result.get("receiver_bank_matches", False))

            # ค้นหา UsedQR ใบนี้ แล้วยัด JSON ใส่เข้าไป
            used_qr = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
            if used_qr:
                used_qr.api_raw_data = json.dumps(api_result["raw_data"], ensure_ascii=False)
        else:
            logger.warning(
                "Verify failed | batch_id=%s | error=%s | user_message=%s | payload_amount=%s",
                batch_id,
                api_result.get("error", "UNKNOWN_ERROR"),
                api_result.get("user_message", ""),
                format_amount(api_result.get("payload_amount")),
            )
            result["api_success"] = False
            result["amounts"].append(api_result.get("payload_amount"))
            error_msg = api_result.get("error", "UNKNOWN_ERROR")
            result["user_error_msg"] = api_result.get(
                "user_message", f"⚠️ Slip verification system error ({error_msg})"
            )

    return result


async def send_manual_review_message(
    bot,
    chat_id: str,
    reply_to_message_id: int,
    batch_id: str,
    body_lines: list[str],
    footer_message: str,
):
    """ข้อความขอให้แอดมินตัดสิน: หัวข้อ -> รายละเอียด -> สิ่งที่ต้องทำต่อ"""
    alert_text = (
        "⚠️ Manual review required\n\n"
        + "\n".join(body_lines)
        + f"\n\n{footer_message}"
    )

    await bot.send_message(
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        text=alert_text,
        reply_markup=get_approval_keyboard(batch_id),
    )


async def download_and_scan_photo(message) -> list[str] | None:
    """โหลดรูปจาก Telegram แล้วอ่าน QR ทุกใบในรูป (คืน None ถ้าโหลดรูปไม่สำเร็จ)"""
    chat_id = message.chat_id
    msg_id = message.message_id
    photo = message.photo[-1]
    temp_path = f"temp_{msg_id}.jpg"

    try:
        photo_file = await photo.get_file(
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )

        await photo_file.download_to_drive(
            custom_path=temp_path,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )

    except TimedOut:
        await message.reply_text(
            "⚠️ Could not download the photo\n\n"
            "Telegram timed out. Please send the slip again."
        )
        return None

    except NetworkError:
        await message.reply_text(
            "⚠️ Could not download the photo\n\n"
            "Connection to Telegram failed. Please send the slip again."
        )
        return None

    try:
        qr_data_list = read_qr_code(temp_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    logger.info("Photo scan result | chat_id=%s | msg_id=%s | qr_count=%s", chat_id, msg_id, len(qr_data_list))
    if not qr_data_list:
        print("📸 [DEBUG] No QR code detected in this image")
        return qr_data_list

    print(f"📸 [DEBUG] Detected {len(qr_data_list)} QR code(s):")
    for idx, qr in enumerate(qr_data_list, 1):
        logger.info(
            "QR payload summary | chat_id=%s | msg_id=%s | index=%s | amount=%s | payload=%s",
            chat_id,
            msg_id,
            idx,
            format_amount(extract_amount_from_qr_payload(qr)),
            summarize_qr_payload(qr),
        )
        print(f"   QR Code {idx} -> Payload: {qr}")

    return qr_data_list


async def collect_media_group_photo(media_group_id, bot, chat_id, msg_id, caption, qr_list):
    """พักรูปที่ส่งมาพร้อมกันในข้อความเดียว (อัลบั้ม) ไว้ก่อน แล้วค่อยประมวลผลรวมทีเดียว

    Telegram ส่งอัลบั้มมาเป็นคนละ message และติด caption มาแค่ใบเดียว
    ถ้าแยกกันประมวลผลจะกลายเป็นคนละรายการและใบที่ไม่มี caption จะไม่มียอดให้เทียบ
    """
    async with _media_group_lock:
        group = _media_groups.get(media_group_id)
        is_first_photo = group is None

        if is_first_photo:
            group = {
                "chat_id": chat_id,
                "msg_id": msg_id,
                "caption": caption,
                "qr_list": [],
                "photo_count": 0,
                "deadline": 0.0,
            }
            _media_groups[media_group_id] = group

        group["qr_list"].extend(qr_list)
        group["photo_count"] += 1

        # ตอบกลับที่ใบที่ติด caption มา (ปกติคือใบแรก แต่ไม่เสมอไป)
        if caption and not group["caption"]:
            group["caption"] = caption
            group["msg_id"] = msg_id

        # ทุกครั้งที่มีรูปใหม่เข้ามา ให้เลื่อนเวลาปิดกลุ่มออกไป
        group["deadline"] = time.monotonic() + MEDIA_GROUP_WAIT_SECONDS

    if is_first_photo:
        asyncio.create_task(flush_media_group(media_group_id, bot))


async def flush_media_group(media_group_id, bot):
    """รอจนไม่มีรูปใหม่เข้ามาแล้ว ค่อยส่งทั้งอัลบั้มไปตรวจเป็นชุดเดียว"""
    try:
        while True:
            async with _media_group_lock:
                group = _media_groups.get(media_group_id)
                if group is None:
                    return

                remaining = group["deadline"] - time.monotonic()
                if remaining <= 0:
                    _media_groups.pop(media_group_id, None)
                    break

            await asyncio.sleep(remaining)

        logger.info(
            "Media group collected | media_group_id=%s | photos=%s | qr_count=%s",
            media_group_id,
            group["photo_count"],
            len(group["qr_list"]),
        )

        if not group["qr_list"]:
            print(f"📸 [DEBUG] Album of {group['photo_count']} photo(s) has no QR code")

        await process_slip_group(
            bot,
            group["chat_id"],
            group["msg_id"],
            group["caption"],
            group["qr_list"],
            group["photo_count"],
        )
    except Exception:
        logger.exception("Failed to process media group | media_group_id=%s", media_group_id)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    print(f"👉 [DEBUG] Received a message from the group with Chat ID: {chat_id}")
    msg_id = message.message_id
    caption = message.caption or ""

    qr_data_list = await download_and_scan_photo(message)
    if qr_data_list is None:
        return

    # หลายรูปในข้อความเดียว: รอให้ครบทั้งอัลบั้มก่อน
    if message.media_group_id:
        await collect_media_group_photo(
            str(message.media_group_id), context.bot, chat_id, msg_id, caption, qr_data_list
        )
        return

    await process_slip_group(context.bot, chat_id, msg_id, caption, qr_data_list, photo_count=1)


async def process_slip_group(bot, chat_id, msg_id, caption: str, qr_list: list[str], photo_count: int):
    """ตรวจสลิปทั้งชุด (1 รูป 1 QR, 1 รูปหลาย QR หรือหลายรูปในข้อความเดียว) เป็นรายการเดียว"""
    # สลิปใบเดียวกันที่ถูกส่งซ้ำในชุดเดียวกัน ต้องนับแค่ครั้งเดียว ไม่งั้นยอดรวมจะเบิ้ล
    qr_data_list = list(dict.fromkeys(qr_list))
    if len(qr_data_list) != len(qr_list):
        logger.info(
            "Removed duplicated QR in the same group | chat_id=%s | msg_id=%s | %s -> %s",
            chat_id, msg_id, len(qr_list), len(qr_data_list),
        )

    with SessionLocal() as db:
        status, txn = process_incoming_slip(db, qr_data_list, chat_id, msg_id, caption)

        if status == "unknown_group":
            logger.warning(
                "Ignored photo from unregistered group | chat_id=%s | msg_id=%s "
                "(set VIP_WE_CHAT_ID / VIP_12_CHAT_ID in .env)",
                chat_id,
                msg_id,
            )
            return

        if status == "duplicate":
            await bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=msg_id,
                text=("⚠️ Duplicate slip\n\n"
                      "This slip has already been submitted, so it was not recorded again."),
            )
            return

        # อ่าน QR ไม่ออก = ตรวจกับ API ไม่ได้เลย ให้แอดมินตัดสินจากข้อมูลในแชท
        if not qr_data_list:
            add_audit_log(db, txn.batch_id, "manual_review_no_qr")
            db.commit()

            logger.info(
                "No QR found, sending for manual review | batch_id=%s | photos=%s "
                "| reported_name=%s | reported_amount=%s",
                txn.batch_id, photo_count, txn.chat_fullname or "-", format_amount(txn.chat_amount),
            )

            await send_manual_review_message(
                bot,
                chat_id,
                msg_id,
                txn.batch_id,
                [
                    f"No QR code could be read from {'this photo' if photo_count == 1 else f'these {photo_count} photos'}, "
                    "so the slip could not be verified.",
                    "",
                    "Details reported in this chat:",
                    f"• ID: {txn.chat_trans_id or txn.chat_user_id or '-'}",
                    f"• Name: {txn.chat_fullname or '-'}",
                    f"• Amount: {format_thb(txn.chat_amount)}",
                ],
                "Receive will ask you to pick the bank account, then save these details to the sheet.",
            )
            return

        print(f"🔎 Verifying {len(qr_data_list)} slip(s) from {photo_count} photo(s) via API...")

        batch_result = verify_slip_batch(db, qr_data_list, txn.batch_id)
        api_success = batch_result["api_success"]
        verified_total_amount, payload_amount_missing = sum_batch_amount(batch_result["amounts"])
        sender_names_str = join_unique(batch_result["senders"])
        receiver_names_str = join_unique(batch_result["receivers"])

        expected_amount = txn.chat_amount
        amount_match = amounts_match(expected_amount, verified_total_amount)
        # หลาย QR ในรูปเดียว หรือหลายรูปในข้อความเดียว = ให้แอดมินตัดสินเสมอ ไม่ auto receive
        multi_slip_batch = len(qr_data_list) > 1 or photo_count > 1

        chat_name = (txn.chat_fullname or "").strip()
        is_name_match = is_reported_name_verified(batch_result["senders"], chat_name)
        bank_matches = all(batch_result["bank_matches"]) if batch_result["bank_matches"] else False

        logger.info(
            "Batch verification summary | batch_id=%s | category=%s | api_success=%s | payload_missing=%s "
            "| verified_total=%s | expected_amount=%s | amount_match=%s | name_match=%s | bank_match=%s",
            txn.batch_id,
            txn.category,
            api_success,
            payload_amount_missing,
            format_amount(verified_total_amount),
            format_amount(expected_amount),
            amount_match,
            is_name_match,
            bank_matches,
        )

        txn.api_total_amount = verified_total_amount
        txn.sender_names = sender_names_str
        txn.receiver_names = receiver_names_str
        if api_success:
            # ทุกสลิปเข้าบัญชีเดียวกัน -> ใช้บัญชีนั้นได้เลย ไม่ต้องถามแอดมินซ้ำ
            txn.receiver_account = unique_receiver_account(batch_result["receivers"]) or receiver_names_str

        verification_decision = determine_verification_action(
            api_success=api_success,
            amount_matches=amount_match,
            name_matches=is_name_match,
            bank_matches=bank_matches,
            multi_slip_batch=multi_slip_batch,
        )

        if verification_decision == VerificationDecision.AUTO_RECEIVE:
            txn.status = "Receive"
            add_audit_log(db, txn.batch_id, "api_verified_matched")
            db.commit()

            sheet_success, sheet_error_msg = append_to_sheet(txn)
            if sheet_success:
                add_audit_log(db, txn.batch_id, "sheet_saved")
                db.commit()
                logger.info(
                    "✅ Auto-Received: Batch=%s | Receiver(s): %s | Account: %s | Amount: %s",
                    txn.batch_id[:15], receiver_names_str, txn.receiver_account or "-", verified_total_amount
                )
                await bot.send_message(
                    chat_id=chat_id,
                    reply_to_message_id=msg_id,
                    text=(f"✅ Received automatically\n\n"
                          f"Sender: {sender_names_str or '-'}\n"
                          f"Account: {txn.receiver_account or '-'}\n"
                          f"Amount: {format_thb(verified_total_amount)}\n\n"
                          f"Saved to today's sheet."),
                )
            else:
                # ปลดล็อกให้กดบันทึกใหม่ได้ หลังแก้ปัญหาชีทเรียบร้อย
                unlock_after_failed_save(db, txn)
                await send_manual_review_message(
                    bot,
                    chat_id,
                    msg_id,
                    txn.batch_id,
                    [
                        "All checks passed, but the slip could not be saved to today's sheet.",
                        "",
                        f"Reason: {str(sheet_error_msg)[:200]}",
                        "",
                        f"Sender: {sender_names_str or '-'}",
                        f"Account: {txn.receiver_account or '-'}",
                        f"Amount: {format_thb(verified_total_amount)}",
                    ],
                    "Fix the sheet, then press Receive to save it again.",
                )
            return

        if verification_decision == VerificationDecision.AUTO_REJECT:
            txn.status = "Reject"
            add_audit_log(db, txn.batch_id, "auto_rejected_mismatch")

            reject_reasons = []
            if not bank_matches:
                reject_reasons.append(
                    "• Receiver account: "
                    + build_bank_mismatch_reason(batch_result["bank_codes"], batch_result["bank_matches"])
                )
            if not amount_match:
                reject_reasons.append(
                    f"• Amount: the slip says {format_thb(verified_total_amount)}, "
                    f"but this chat reported {format_thb(expected_amount)}"
                )

            logger.info(
                "❌ Auto-Rejected: Batch=%s | reasons=%s",
                txn.batch_id[:15],
                " / ".join(reject_reasons) or "unknown",
            )
            await bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=msg_id,
                text=("❌ Rejected automatically\n\n"
                      + ("\n".join(reject_reasons) or "• The slip details do not match this chat")
                      + "\n\nNothing was saved to the sheet. "
                        "Correct the details and send the slip again."),
            )
            return

        # --- Manual review: ข้อมูลไม่พอตัดสิน หรือติดแค่เรื่องชื่อ ---
        add_audit_log(db, txn.batch_id, "manual_review_required")
        db.commit()

        body_lines = []
        if not api_success:
            body_lines.append(f"Slip verification failed: {batch_result['user_error_msg']}")
        if multi_slip_batch:
            slip_word = "slip" if len(qr_data_list) == 1 else "slips"
            photo_word = "photo" if photo_count == 1 else "photos"
            body_lines.append(
                f"{len(qr_data_list)} {slip_word} in {photo_count} {photo_word}, "
                f"totalling {format_thb(verified_total_amount)}. "
                "Multiple slips are never received automatically."
            )
        if body_lines:
            body_lines.append("")

        body_lines.extend([
            format_check_line(
                "Name", is_name_match,
                sender_names_str or "-",
                f"slip says {sender_names_str or 'unknown'}, this chat reported {chat_name or 'nothing'}",
            ),
            format_check_line(
                "Amount", amount_match,
                format_thb(verified_total_amount),
                f"slip says {format_thb(verified_total_amount)}, "
                f"this chat reported {format_thb(expected_amount)}",
            ),
            format_check_line(
                "Receiver account", bank_matches,
                receiver_names_str or "-",
                build_bank_mismatch_reason(batch_result["bank_codes"], batch_result["bank_matches"]),
            ),
        ])

        await send_manual_review_message(
            bot,
            chat_id,
            msg_id,
            txn.batch_id,
            body_lines,
            "Choose Receive to save it to today's sheet, or Reject to discard it.",
        )


SLIP_NOT_FOUND_TEXT = "This slip is no longer in the system. Please send it again."


async def save_receive_to_sheet(query, db, txn, bank_value: str):
    """บันทึกรายการที่แอดมินกดรับลงชีท แล้วรายงานผลกลับไปที่ข้อความเดิม"""
    if is_sheet_locked(db, txn.batch_id):
        await query.answer("This slip has already been saved.", show_alert=True)
        await query.edit_message_text(
            text=(f"✅ Already saved\n\n"
                  f"Account: {bank_value}\n"
                  f"Amount: {describe_txn_amount(txn)}"),
            reply_markup=None,
        )
        return

    add_audit_log(db, txn.batch_id, "saving_started")
    await query.edit_message_text(
        text=f"Saving to today's sheet...\n\nAccount: {bank_value}",
        reply_markup=None,
    )

    sheet_success, sheet_error_msg = append_to_sheet(txn)

    if sheet_success:
        add_audit_log(db, txn.batch_id, "sheet_saved")
        db.commit()
        await query.edit_message_text(
            text=(f"✅ Received\n\n"
                  f"Account: {bank_value}\n"
                  f"Amount: {describe_txn_amount(txn)}\n\n"
                  f"Saved to today's sheet."),
            reply_markup=None,
        )
    else:
        # ปลดล็อกให้กดใหม่ได้ ไม่ค้างสถานะจนส่งสลิปซ้ำก็ไม่ได้
        unlock_after_failed_save(db, txn)
        await query.edit_message_text(
            text=("⚠️ Could not save to today's sheet\n\n"
                  f"{str(sheet_error_msg)[:200]}\n\n"
                  "Fix the sheet, then press Receive to try again."),
            reply_markup=get_approval_keyboard(txn.batch_id),
        )


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    callback_data = query.data or ""
    await query.answer()

    with SessionLocal() as db:
        if callback_data.startswith("bank_"):
            _, short_ref, bank_value = callback_data.split("_", 2)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                await query.edit_message_text(text=SLIP_NOT_FOUND_TEXT, reply_markup=None)
                return

            txn.chat_bank = bank_value
            txn.receiver_account = bank_value
            txn.status = "Receive"
            add_audit_log(db, txn.batch_id, "manual_bank_selected")
            await save_receive_to_sheet(query, db, txn, bank_value)
            return

        if callback_data.startswith("back_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if txn:
                await query.answer("Back to Receive / Reject")
                await query.edit_message_reply_markup(reply_markup=get_approval_keyboard(txn.batch_id))
            return

        action, short_ref = callback_data.split('_', 1)
        txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()

        if not txn:
            await query.answer("Slip not found.", show_alert=True)
            await query.edit_message_text(text=SLIP_NOT_FOUND_TEXT, reply_markup=None)
            return

        if txn.status in ["Receive", "Reject"]:
            already_received = txn.status == "Receive"
            await query.answer(
                f"This slip was already {'received' if already_received else 'rejected'}.",
                show_alert=True,
            )
            await query.edit_message_text(
                text=("✅ Already received" if already_received else "❌ Already rejected"),
                reply_markup=None,
            )
            return

        if action == "reject":
            txn.status = "Reject"
            add_audit_log(db, txn.batch_id, "admin_rejected")
            await query.answer("Slip rejected")
            await query.edit_message_text(
                text=("❌ Rejected\n\n"
                      "Nothing was saved to the sheet. "
                      "Send the corrected slip again if needed."),
                reply_markup=None,
            )
            return

        # Receive: ใช้บัญชีที่อ่านได้จากสลิป ถ้าไม่รู้ค่อยให้แอดมินเลือกเอง
        txn.status = "Receive"
        known_bank = resolve_known_bank_value(txn)

        if action == "agent" or not known_bank:
            # สลับแค่ปุ่ม ไม่ทับข้อความผลตรวจ กด Back แล้วจะได้ข้อความเดิมครบ
            await query.answer("Choose the bank account for this slip")
            await query.edit_message_reply_markup(
                reply_markup=get_bank_selection_keyboard(txn.batch_id)
            )
            return

        txn.chat_bank = known_bank
        add_audit_log(db, txn.batch_id, "bank_taken_from_slip")
        await save_receive_to_sheet(query, db, txn, known_bank)
