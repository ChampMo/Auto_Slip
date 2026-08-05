import asyncio
import os
import logging
import time
from telegram import Update
from telegram.ext import ContextTypes
from core.captions import parse_typed_amount
from core.config import config
from core.scanner import read_qr_code
from bot.keyboards import get_approval_keyboard, get_bank_selection_keyboard
from bot.verification_flow import VerificationDecision, determine_verification_action
from database.session import SessionLocal
from core.matcher import process_incoming_slip
from core.names import split_bank_name
from services.easyslip import verify_slip, extract_amount_from_qr_payload, BANK_DROPDOWN_VALUES
from database.crud import (
    add_audit_log,
    claim_transaction,
    get_approver_ids,
    is_sheet_locked,
    DECIDED_STATUSES,
    SHEET_REOPEN_ACTION,
)
from services.gsheets import append_to_sheet, SheetEntry
import json
from database.models import Transaction, UsedQR
from telegram.error import TimedOut, NetworkError

logger = logging.getLogger(__name__)

# เวลารอรูปใบถัดไปของอัลบั้มเดียวกัน (Telegram ส่งมาติดๆ กันเป็นคนละ message)
MEDIA_GROUP_WAIT_SECONDS = 2.0
_media_groups: dict[str, dict] = {}
_media_group_lock = asyncio.Lock()

# คำขอให้แอดมินพิมพ์ยอดเอง เก็บไว้ในหน่วยความจำ (บอทรีสตาร์ทแล้วหาย ให้กด Receive ใหม่)
AMOUNT_REQUEST_TTL_SECONDS = 30 * 60
_amount_requests: dict[str, dict] = {}


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


def verify_slip_batch(qr_list: list[str], batch_id: str) -> dict:
    """ยิง API ตรวจสอบสลิปทุกใบในรูปเดียวกัน แล้วรวมผลลัพธ์เป็นก้อนเดียว

    ไม่ยุ่งกับฐานข้อมูลเลย เพราะฟังก์ชันนี้ถูกเรียกจาก worker thread
    (session ของ SQLAlchemy ใช้ข้ามเธรดไม่ได้) ผู้เรียกเอา raw_data ไปบันทึกเองทีหลัง
    """
    result = {
        "api_success": True,
        "amounts": [],
        "senders": [],
        "receivers": [],
        "bank_codes": [],
        "bank_matches": [],
        "raw_data_by_qr": {},
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

            result["raw_data_by_qr"][qr] = json.dumps(api_result["raw_data"], ensure_ascii=False)
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

    sent = await bot.send_message(
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        text=alert_text,
        reply_markup=get_approval_keyboard(batch_id),
    )
    remember_review_message(batch_id, getattr(sent, "message_id", None))
    return sent


def remember_review_message(batch_id: str, review_msg_id) -> None:
    """จำว่าปุ่มอยู่บนข้อความไหน จะได้ตามไปปิดปุ่มได้ตอนสลิปถูกตัดสินแล้ว

    เก็บใน DB ไม่ใช่ในหน่วยความจำ เพราะปุ่มอยู่บนจอได้ข้ามการรีสตาร์ทบอท
    """
    if review_msg_id is None:
        return
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        if txn is not None:
            txn.review_msg_id = str(review_msg_id)
            db.commit()


async def close_review_buttons(bot, chat_id, review_msg_id, skip_msg_id=None) -> None:
    """ปิดปุ่มบนข้อความตรวจสอบใบเดิม หลังสลิปจบไปแล้ว

    ถ้าไม่ปิด ปุ่มชุดเดิมจะยังค้างให้กดได้ทั้งที่รายการจบแล้ว
    (กดไปก็ไม่เสียหาย เพราะมีด่านเช็คสถานะ แต่ทำให้คนในกลุ่มสับสน)
    """
    if not review_msg_id or bot is None:
        return
    if skip_msg_id is not None and str(review_msg_id) == str(skip_msg_id):
        return  # เป็นข้อความเดียวกับที่เพิ่งแก้ไป ปุ่มถูกเอาออกแล้ว

    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=int(review_msg_id), reply_markup=None
        )
    except Exception as exc:
        # ข้อความอาจถูกลบ หรือปุ่มถูกปิดไปแล้ว ไม่ใช่เรื่องคอขาดบาดตาย
        logger.info("Could not close review buttons | msg_id=%s | %s", review_msg_id, exc)


def pick_image_source(message):
    """เลือกไฟล์รูปจากข้อความ รองรับทั้งรูปปกติและรูปที่ส่งมาแบบไฟล์

    คนมักส่งสลิปแบบ 'ส่งเป็นไฟล์' เพื่อไม่ให้ภาพแตก ซึ่งมาเป็น document ไม่ใช่ photo
    """
    if getattr(message, "photo", None):
        return message.photo[-1]

    document = getattr(message, "document", None)
    if document is not None and str(getattr(document, "mime_type", "") or "").startswith("image/"):
        return document

    return None


async def download_and_scan_photo(message) -> list[str] | None:
    """โหลดรูปจาก Telegram แล้วอ่าน QR ทุกใบในรูป (คืน None ถ้าโหลดรูปไม่สำเร็จ)"""
    chat_id = message.chat_id
    msg_id = message.message_id
    photo = pick_image_source(message)
    if photo is None:
        logger.info("Message has no image to scan | chat_id=%s | msg_id=%s", chat_id, msg_id)
        return None
    temp_path = f"temp_{chat_id}_{msg_id}.img"

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


async def flush_pending_media_groups(bot):
    """เคลียร์อัลบั้มที่ยังค้างในคิวก่อนบอทปิดตัว

    ปกติอัลบั้มจะรอ 2 วินาทีให้รูปมาครบ ถ้าบอทถูกสั่งปิดในจังหวะนั้นพอดี
    รูปชุดนั้นจะหายไปเงียบๆ — เรียกตัวนี้ตอน shutdown เพื่อประมวลผลให้จบก่อน
    """
    async with _media_group_lock:
        pending = list(_media_groups.items())
        _media_groups.clear()

    if not pending:
        return

    logger.info("Flushing %s pending media group(s) before shutdown", len(pending))
    for media_group_id, group in pending:
        if not group["qr_list"]:
            logger.warning(
                "Dropping album with no QR at shutdown | media_group_id=%s | photos=%s",
                media_group_id, group["photo_count"],
            )
            continue
        try:
            await process_slip_group(
                bot, group["chat_id"], group["msg_id"], group["caption"],
                group["qr_list"], group["photo_count"],
            )
        except Exception:
            logger.exception("Failed to flush media group at shutdown | media_group_id=%s", media_group_id)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    print(f"👉 [DEBUG] Received a message from the group with Chat ID: {chat_id}")
    msg_id = message.message_id
    caption = message.caption or ""

    # รูปเดี่ยวที่ไม่มีข้อความกำกับ ตัดทิ้งตั้งแต่ยังไม่โหลดไฟล์
    # (อัลบั้มตัดตรงนี้ไม่ได้ เพราะ Telegram ใส่ caption มาแค่ใบเดียว ต้องรอดูทั้งชุดก่อน)
    if not message.media_group_id and not has_usable_caption(caption):
        logger.info("Ignored photo with no caption | chat_id=%s | msg_id=%s", chat_id, msg_id)
        return

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


def has_usable_caption(caption: str) -> bool:
    """รูปที่ไม่มีข้อความกำกับ ถือว่าไม่ใช่การส่งสลิป

    ในกลุ่มมีการแชร์ภาพหน้าจอกันเป็นปกติ ซึ่งบางทีก็มี QR ของสลิปติดอยู่ในภาพด้วย
    ถ้าไม่กรองออก บอทจะไปสร้างรายการจากภาพพวกนั้นแล้วจบลงที่แถวขยะในชีท
    """
    return bool((caption or "").strip())


async def process_slip_group(bot, chat_id, msg_id, caption: str, qr_list: list[str], photo_count: int):
    """ตรวจสลิปทั้งชุด (1 รูป 1 QR, 1 รูปหลาย QR หรือหลายรูปในข้อความเดียว) เป็นรายการเดียว

    แบ่ง DB session เป็นช่วงสั้นๆ ไม่ถือ connection ค้างระหว่างรอ EasySlip หรือ Google
    ซึ่งรวมกันอาจกินเวลาหลายสิบวินาทีต่อสลิปหนึ่งชุด
    """
    if not has_usable_caption(caption):
        logger.info(
            "Ignored photo with no caption | chat_id=%s | msg_id=%s | photos=%s | qr_count=%s",
            chat_id, msg_id, photo_count, len(qr_list),
        )
        return

    # สลิปใบเดียวกันที่ถูกส่งซ้ำในชุดเดียวกัน ต้องนับแค่ครั้งเดียว ไม่งั้นยอดรวมจะเบิ้ล
    qr_data_list = list(dict.fromkeys(qr_list))
    if len(qr_data_list) != len(qr_list):
        logger.info(
            "Removed duplicated QR in the same group | chat_id=%s | msg_id=%s | %s -> %s",
            chat_id, msg_id, len(qr_list), len(qr_data_list),
        )

    # ── ช่วงที่ 1: รับเข้าระบบ + เช็คซ้ำ ─────────────────────────────
    reported = None
    with SessionLocal() as db:
        status, txn = process_incoming_slip(db, qr_data_list, chat_id, msg_id, caption)
        if txn is not None:
            # คัดค่าออกมาก่อนปิด session จะได้ใช้ต่อได้โดยไม่ต้องถือ connection ไว้
            reported = {
                "batch_id": txn.batch_id,
                "amount": txn.chat_amount,
                "name": (txn.chat_fullname or "").strip(),
                "id": txn.chat_trans_id or txn.chat_user_id or "-",
                "warning": txn.caption_warning or "",
            }

    if status == "unknown_group":
        logger.warning(
            "Ignored photo from unregistered group | chat_id=%s | msg_id=%s "
            "(set VIP_WE_CHAT_ID / VIP_12_CHAT_ID in .env)",
            chat_id, msg_id,
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

    batch_id = reported["batch_id"]

    # ── อ่าน QR ไม่ออก และแชทก็ไม่ได้บอกยอด = ไม่เหลืออะไรให้ตรวจหรือบันทึกเลย ──
    if not qr_data_list and reported["amount"] is None:
        photo_word = "this photo" if photo_count == 1 else f"these {photo_count} photos"
        with SessionLocal() as db:
            txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
            if txn is not None:
                txn.status = "Reject"
            add_audit_log(db, batch_id, "auto_rejected_no_qr_no_amount")

        logger.info(
            "Rejected: no QR and no amount | batch_id=%s | photos=%s", batch_id, photo_count,
        )
        await bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=msg_id,
            text=("❌ Rejected automatically\n\n"
                  f"• No QR code could be read from {photo_word}\n"
                  "• No amount was found in the message\n\n"
                  "Nothing was saved to the sheet. Send the slip again with the amount "
                  "written in the message."),
        )
        return

    # ── อ่าน QR ไม่ออก แต่ยังมียอดจากแชท = ให้แอดมินตัดสินจากข้อมูลที่มี ──
    if not qr_data_list:
        with SessionLocal() as db:
            add_audit_log(db, batch_id, "manual_review_no_qr")

        logger.info(
            "No QR found, sending for manual review | batch_id=%s | photos=%s "
            "| reported_name=%s | reported_amount=%s",
            batch_id, photo_count, reported["name"] or "-", format_amount(reported["amount"]),
        )

        no_qr_lines = [
            f"No QR code could be read from {'this photo' if photo_count == 1 else f'these {photo_count} photos'}, "
            "so the slip could not be verified.",
        ]
        if reported["warning"]:
            no_qr_lines.append(f"⚠️ {reported['warning']}")
        no_qr_lines.extend([
            "",
            "Details reported in this chat:",
            f"• ID: {reported['id']}",
            f"• Name: {reported['name'] or '-'}",
            f"• Amount: {format_thb(reported['amount'])}",
        ])

        await send_manual_review_message(
            bot, chat_id, msg_id, batch_id, no_qr_lines,
            "Receive will ask you to pick the bank account, then save these details to the sheet.",
        )
        return

    # ── ช่วงที่ 2: ยิง API ตรวจสลิป (ไม่ถือ session และไม่แช่แข็ง event loop) ──
    print(f"🔎 Verifying {len(qr_data_list)} slip(s) from {photo_count} photo(s) via API...")
    batch_result = await asyncio.to_thread(verify_slip_batch, qr_data_list, batch_id)

    api_success = batch_result["api_success"]
    verified_total_amount, payload_amount_missing = sum_batch_amount(batch_result["amounts"])
    sender_names_str = join_unique(batch_result["senders"])
    receiver_names_str = join_unique(batch_result["receivers"])

    expected_amount = reported["amount"]
    chat_name = reported["name"]
    amount_match = amounts_match(expected_amount, verified_total_amount)
    # หลาย QR ในรูปเดียว หรือหลายรูปในข้อความเดียว = ให้แอดมินตัดสินเสมอ ไม่ auto receive
    multi_slip_batch = len(qr_data_list) > 1 or photo_count > 1
    is_name_match = is_reported_name_verified(batch_result["senders"], chat_name)
    bank_matches = all(batch_result["bank_matches"]) if batch_result["bank_matches"] else False

    logger.info(
        "Batch verification summary | batch_id=%s | api_success=%s | payload_missing=%s "
        "| verified_total=%s | expected_amount=%s | amount_match=%s | name_match=%s | bank_match=%s",
        batch_id, api_success, payload_amount_missing,
        format_amount(verified_total_amount), format_amount(expected_amount),
        amount_match, is_name_match, bank_matches,
    )

    verification_decision = determine_verification_action(
        api_success=api_success,
        amount_matches=amount_match,
        name_matches=is_name_match,
        bank_matches=bank_matches,
        multi_slip_batch=multi_slip_batch,
        caption_unreliable=bool(reported["warning"]),
    )

    # ── ช่วงที่ 3: บันทึกผลตรวจและตัดสิน ─────────────────────────────
    sheet_entry = None
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        if txn is None:
            logger.error("Transaction disappeared before saving results | batch_id=%s", batch_id)
            return

        for qr, raw_data in batch_result["raw_data_by_qr"].items():
            used_qr = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
            if used_qr:
                used_qr.api_raw_data = raw_data

        txn.api_total_amount = verified_total_amount
        txn.sender_names = sender_names_str
        txn.receiver_names = receiver_names_str
        if api_success:
            # ทุกสลิปเข้าบัญชีเดียวกัน -> ใช้บัญชีนั้นได้เลย ไม่ต้องถามแอดมินซ้ำ
            txn.receiver_account = unique_receiver_account(batch_result["receivers"]) or receiver_names_str

        if verification_decision == VerificationDecision.AUTO_RECEIVE:
            txn.status = "Receive"
            add_audit_log(db, batch_id, "api_verified_matched")
            sheet_entry = SheetEntry.from_transaction(txn)
        elif verification_decision == VerificationDecision.AUTO_REJECT:
            txn.status = "Reject"
            add_audit_log(db, batch_id, "auto_rejected_mismatch")
        else:
            add_audit_log(db, batch_id, "manual_review_required")

        receiver_account = txn.receiver_account

    # ── ช่วงที่ 4: ลงมือตามผลตัดสิน ─────────────────────────────────
    if verification_decision == VerificationDecision.AUTO_RECEIVE:
        sheet_success, sheet_error_msg = await asyncio.to_thread(append_to_sheet, sheet_entry)

        with SessionLocal() as db:
            if sheet_success:
                add_audit_log(db, batch_id, "sheet_saved")
            else:
                txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
                if txn is not None:
                    # ปลดล็อกให้กดบันทึกใหม่ได้ หลังแก้ปัญหาชีทเรียบร้อย
                    unlock_after_failed_save(db, txn)

        if sheet_success:
            logger.info(
                "✅ Auto-Received: Batch=%s | Receiver(s): %s | Account: %s | Amount: %s",
                batch_id[:15], receiver_names_str, receiver_account or "-", verified_total_amount,
            )
            await bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=msg_id,
                text=(f"✅ Received automatically\n\n"
                      f"Sender: {sender_names_str or '-'}\n"
                      f"Account: {receiver_account or '-'}\n"
                      f"Amount: {format_thb(verified_total_amount)}\n\n"
                      f"Saved to today's sheet."),
            )
        else:
            await send_manual_review_message(
                bot, chat_id, msg_id, batch_id,
                [
                    "All checks passed, but the slip could not be saved to today's sheet.",
                    "",
                    f"Reason: {str(sheet_error_msg)[:200]}",
                    "",
                    f"Sender: {sender_names_str or '-'}",
                    f"Account: {receiver_account or '-'}",
                    f"Amount: {format_thb(verified_total_amount)}",
                ],
                "Fix the sheet, then press Receive to save it again.",
            )
        return

    if verification_decision == VerificationDecision.AUTO_REJECT:
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
            batch_id[:15], " / ".join(reject_reasons) or "unknown",
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
    body_lines = []
    if reported["warning"]:
        body_lines.append(f"⚠️ {reported['warning']}")
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

    if verified_total_amount is None and expected_amount is None:
        footer_message = (
            "No amount is known for this slip — Receive will ask you to type it in. "
            "Reject discards it."
        )
    else:
        footer_message = "Choose Receive to save it to today's sheet, or Reject to discard it."

    await send_manual_review_message(
        bot, chat_id, msg_id, batch_id, body_lines, footer_message,
    )


SLIP_NOT_FOUND_TEXT = "This slip is no longer in the system. Please send it again."


def amount_is_unknown(txn) -> bool:
    """ไม่รู้ยอดเลยสักทาง ทั้งจากสลิปและจากข้อความในแชท"""
    return txn.api_total_amount is None and txn.chat_amount is None


def _amount_request_key(chat_id, msg_id) -> str:
    # message_id ซ้ำกันได้ข้ามกลุ่ม จึงต้องผูกกับ chat_id ด้วย
    return f"{chat_id}:{msg_id}"


def remember_amount_request(chat_id, question_msg_id, batch_id: str, user_id) -> None:
    """จำไว้ว่าข้อความคำถามใบไหน กำลังรอยอดของสลิปใบไหน จากใคร"""
    now = time.monotonic()
    for key, request in list(_amount_requests.items()):
        if request["expires_at"] <= now:
            _amount_requests.pop(key, None)

    _amount_requests[_amount_request_key(chat_id, question_msg_id)] = {
        "batch_id": batch_id,
        "user_id": str(user_id),
        "expires_at": now + AMOUNT_REQUEST_TTL_SECONDS,
    }


def find_amount_request(chat_id, question_msg_id) -> dict | None:
    request = _amount_requests.get(_amount_request_key(chat_id, question_msg_id))
    if request is None:
        return None
    if request["expires_at"] <= time.monotonic():
        _amount_requests.pop(_amount_request_key(chat_id, question_msg_id), None)
        return None
    return request


async def ask_for_amount(query, txn) -> None:
    """สลิปที่ไม่รู้ยอดเลย ให้แอดมินพิมพ์ยอดเองก่อน ค่อยไปต่อ

    ยังไม่จองสถานะตรงนี้ เพราะถ้าเขาไม่ตอบกลับมา สลิปจะค้างอยู่ในสถานะ Receive ตลอดกาล
    """
    await query.answer("This slip has no amount yet")
    question = await query.message.reply_text(
        "💬 Amount needed\n\n"
        f"{describe_user(query.from_user)}, this slip has no amount — "
        "the slip could not be read and the message did not say one.\n\n"
        "Reply to this message with the amount in THB (for example: 1500).",
    )
    remember_amount_request(
        query.message.chat_id, question.message_id, txn.batch_id, query.from_user.id
    )
    logger.info(
        "Asked for a manual amount | batch_id=%s | user=%s",
        txn.batch_id, describe_actor(query.from_user),
    )


async def handle_amount_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """รับยอดที่แอดมินพิมพ์ตอบกลับมา แล้วพาไปขั้นตอนเลือกธนาคารต่อ"""
    message = update.message
    if message is None or message.reply_to_message is None:
        return

    request = find_amount_request(message.chat_id, message.reply_to_message.message_id)
    if request is None:
        return

    user = message.from_user
    if user is None or str(user.id) != request["user_id"]:
        # คนอื่นตอบแทนไม่ได้ แต่กด Receive เองเพื่อขอยอดของตัวเองได้
        await message.reply_text(
            "This amount was requested from someone else. "
            "Press Receive on the slip to enter it yourself."
        )
        return

    amount = parse_typed_amount(message.text)
    if amount is None:
        await message.reply_text(
            "⚠️ That is not a valid amount\n\n"
            "Reply again with a number greater than zero (for example: 1500)."
        )
        return

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == request["batch_id"]).first()
        if txn is None:
            _amount_requests.pop(_amount_request_key(message.chat_id, message.reply_to_message.message_id), None)
            await message.reply_text(SLIP_NOT_FOUND_TEXT)
            return

        if txn.status in DECIDED_STATUSES:
            _amount_requests.pop(_amount_request_key(message.chat_id, message.reply_to_message.message_id), None)
            await message.reply_text(
                f"This slip was already {'received' if txn.status == 'Receive' else 'rejected'}. "
                "The amount was not changed."
            )
            return

        txn.chat_amount = amount
        add_audit_log(db, txn.batch_id, "amount_entered_manually", actor=describe_actor(user))
        db.commit()
        batch_id = txn.batch_id

    _amount_requests.pop(_amount_request_key(message.chat_id, message.reply_to_message.message_id), None)
    logger.info(
        "Manual amount accepted | batch_id=%s | amount=%s | user=%s",
        batch_id, format_amount(amount), describe_actor(user),
    )

    await message.reply_text(
        f"Amount set to {format_thb(amount)}\n\n"
        "Now choose the bank account this slip was paid into:",
        reply_markup=get_bank_selection_keyboard(batch_id),
    )


def describe_user(user) -> str:
    """ชื่อคนกดปุ่มสำหรับแสดงในข้อความ"""
    if user is None:
        return "unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = getattr(user, "full_name", None) or getattr(user, "first_name", None)
    return name or f"id {user.id}"


def describe_actor(user) -> str:
    """ตัวระบุคนกดสำหรับเก็บลง audit log — ใส่ id ไว้ด้วยเพราะชื่อกับ username เปลี่ยนได้"""
    if user is None:
        return "unknown"
    return f"{user.id} {describe_user(user)}"


def load_approver_ids() -> frozenset:
    """รายชื่อคนที่กดปุ่มได้ = เจ้าของจาก .env + คนที่ถูกเพิ่มไว้ใน DB

    เจ้าของใน .env เป็นกุญแจสำรอง ลบผ่านคำสั่งไม่ได้ กันกรณี DB ว่างหรือคนสุดท้ายเผลอลบตัวเอง
    """
    try:
        with SessionLocal() as db:
            stored_ids = get_approver_ids(db)
    except Exception:
        logger.exception("Could not read the approver list from the database")
        stored_ids = set()

    return frozenset(config.SLIP_APPROVER_IDS) | stored_ids


def is_owner(user) -> bool:
    """เจ้าของที่ตั้งไว้ใน .env — ถอดออกด้วยคำสั่งไม่ได้"""
    return user is not None and str(user.id) in config.SLIP_APPROVER_IDS


def is_approver(user) -> bool:
    """กดปุ่มได้เฉพาะคนที่อยู่ในรายชื่อเท่านั้น"""
    if user is None:
        return False
    return str(user.id) in load_approver_ids()


async def announce_already_decided(query, txn):
    """บอกว่ารายการนี้ถูกตัดสินไปแล้ว (กดซ้ำเอง หรือแอดมินอีกคนกดตัดหน้า)"""
    already_received = str(txn.status) == "Receive"
    await query.answer(
        f"This slip was already {'received' if already_received else 'rejected'}.",
        show_alert=True,
    )
    await query.edit_message_text(
        text=("✅ Already received" if already_received else "❌ Already rejected"),
        reply_markup=None,
    )


async def write_entry_to_sheet(txn):
    """เขียนลงชีทใน worker thread เพื่อไม่ให้บอททั้งตัวค้างระหว่างรอ Google

    ต้องคัดลอกค่าออกจาก ORM object ก่อนส่งข้ามเธรด (session ของ SQLAlchemy ไม่ thread-safe)
    """
    entry = SheetEntry.from_transaction(txn)
    return await asyncio.to_thread(append_to_sheet, entry)


async def save_receive_to_sheet(query, db, txn, bank_value: str, bot=None):
    """บันทึกรายการที่แอดมินกดรับลงชีท แล้วรายงานผลกลับไปที่ข้อความเดิม"""
    chat_id = txn.chat_id
    review_msg_id = txn.review_msg_id
    current_msg_id = getattr(getattr(query, "message", None), "message_id", None)

    if is_sheet_locked(db, txn.batch_id):
        await query.answer("This slip has already been saved.", show_alert=True)
        await query.edit_message_text(
            text=(f"✅ Already saved\n\n"
                  f"Account: {bank_value}\n"
                  f"Amount: {describe_txn_amount(txn)}"),
            reply_markup=None,
        )
        return

    actor = describe_actor(query.from_user)
    add_audit_log(db, txn.batch_id, "saving_started", actor=actor)
    await query.answer("Saving to today's sheet...")
    await query.edit_message_text(
        text=f"Saving to today's sheet...\n\nAccount: {bank_value}",
        reply_markup=None,
    )

    sheet_success, sheet_error_msg = await write_entry_to_sheet(txn)

    if sheet_success:
        add_audit_log(db, txn.batch_id, "sheet_saved", actor=actor)
        db.commit()
        await query.edit_message_text(
            text=(f"✅ Received\n\n"
                  f"Account: {bank_value}\n"
                  f"Amount: {describe_txn_amount(txn)}\n"
                  f"Approved by: {describe_user(query.from_user)}\n\n"
                  f"Saved to today's sheet."),
            reply_markup=None,
        )
        # จบแล้ว ปุ่มชุดเดิมที่ยังค้างอยู่บนข้อความตรวจสอบต้องหายไปด้วย
        await close_review_buttons(bot, chat_id, review_msg_id, skip_msg_id=current_msg_id)
    else:
        # ปลดล็อกให้กดใหม่ได้ ไม่ค้างสถานะจนส่งสลิปซ้ำก็ไม่ได้
        unlock_after_failed_save(db, txn)
        await query.edit_message_text(
            text=("⚠️ Could not save to today's sheet\n\n"
                  f"{str(sheet_error_msg)[:200]}\n\n"
                  "Fix the sheet, then press Receive to try again."),
            reply_markup=get_approval_keyboard(txn.batch_id),
        )
        # ปุ่มย้ายมาอยู่ข้อความนี้แล้ว ปุ่มเก่าต้องปิด ไม่งั้นจะมีปุ่มลอยอยู่ 2 ชุด
        await close_review_buttons(bot, chat_id, review_msg_id, skip_msg_id=current_msg_id)
        txn.review_msg_id = str(current_msg_id) if current_msg_id is not None else review_msg_id
        db.commit()


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    callback_data = query.data or ""
    user = query.from_user

    # ยามอยู่ตรงนี้จุดเดียว จึงคุมครบทุกปุ่ม ทั้ง Receive/Reject, เลือกธนาคาร และ Back
    approver_ids = load_approver_ids()

    if not approver_ids:
        logger.error(
            "Button pressed but no approver is configured | user=%s | data=%s",
            describe_actor(user), callback_data,
        )
        await query.answer(
            "No one is allowed to approve slips yet. "
            "Ask the bot owner to set the approver list.",
            show_alert=True,
        )
        return

    if str(user.id) not in approver_ids:
        logger.warning(
            "Unauthorized button press | user=%s | data=%s",
            describe_actor(user), callback_data,
        )
        await query.answer(
            "You are not allowed to approve slips. "
            "Ask an approver to handle this one.",
            show_alert=True,
        )
        return

    logger.info("Button pressed | user=%s | data=%s", describe_actor(user), callback_data)

    with SessionLocal() as db:
        if callback_data.startswith("bank_"):
            _, short_ref, bank_value = callback_data.split("_", 2)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                await query.edit_message_text(text=SLIP_NOT_FOUND_TEXT, reply_markup=None)
                return

            # ปุ่มเลือกธนาคารอาจค้างอยู่บนจอของแอดมินอีกคน แม้รายการจะถูกตัดสินไปแล้ว
            if not claim_transaction(db, txn.batch_id, "Receive"):
                await announce_already_decided(query, txn)
                return

            txn.chat_bank = bank_value
            txn.receiver_account = bank_value
            add_audit_log(db, txn.batch_id, "manual_bank_selected", actor=describe_actor(user))
            await save_receive_to_sheet(query, db, txn, bank_value, bot=context.bot)
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

        if txn.status in DECIDED_STATUSES:
            await announce_already_decided(query, txn)
            return

        if action == "reject":
            if not claim_transaction(db, txn.batch_id, "Reject"):
                await announce_already_decided(query, txn)
                return

            add_audit_log(db, txn.batch_id, "admin_rejected", actor=describe_actor(user))
            await query.answer("Slip rejected")
            await query.edit_message_text(
                text=("❌ Rejected\n\n"
                      f"Rejected by: {describe_user(user)}\n\n"
                      "Nothing was saved to the sheet. "
                      "Send the corrected slip again if needed."),
                reply_markup=None,
            )
            await close_review_buttons(
                context.bot, txn.chat_id, txn.review_msg_id,
                skip_msg_id=getattr(getattr(query, "message", None), "message_id", None),
            )
            return

        # ไม่รู้ยอดเลย บันทึกไปก็ได้แถวที่ช่องจำนวนเงินว่าง ต้องให้พิมพ์ยอดมาก่อน
        if amount_is_unknown(txn):
            await ask_for_amount(query, txn)
            return

        # Receive: ใช้บัญชีที่อ่านได้จากสลิป ถ้าไม่รู้ค่อยให้แอดมินเลือกเอง
        known_bank = resolve_known_bank_value(txn)

        if action == "agent" or not known_bank:
            # ยังไม่จองสิทธิ์ตรงนี้ เพราะยังไม่ได้ตัดสินอะไร แค่ขอให้เลือกธนาคารก่อน
            # สลับแค่ปุ่ม ไม่ทับข้อความผลตรวจ กด Back แล้วจะได้ข้อความเดิมครบ
            await query.answer("Choose the bank account for this slip")
            await query.edit_message_reply_markup(
                reply_markup=get_bank_selection_keyboard(txn.batch_id)
            )
            return

        if not claim_transaction(db, txn.batch_id, "Receive"):
            await announce_already_decided(query, txn)
            return

        txn.chat_bank = known_bank
        add_audit_log(db, txn.batch_id, "bank_taken_from_slip", actor=describe_actor(user))
        await save_receive_to_sheet(query, db, txn, known_bank, bot=context.bot)
