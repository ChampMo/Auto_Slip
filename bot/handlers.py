import os
import logging
import re
from telegram import Update
from telegram.ext import ContextTypes
from core.scanner import read_qr_code
from bot.keyboards import get_approval_keyboard, get_bank_selection_keyboard
from bot.verification_flow import VerificationDecision, determine_verification_action
from database.session import SessionLocal
from core.matcher import process_incoming_slip
from services.easyslip import verify_slip
from database.crud import add_audit_log, is_sheet_saved, is_sheet_locked, remove_transaction_and_qr_links
from services.gsheets import append_to_sheet
import json
from database.models import UsedQR
from telegram.error import TimedOut, NetworkError

logger = logging.getLogger(__name__)


def normalize_bank_name(value: str) -> str:
    cleaned = (value or "").strip()
    for prefix in ["นาย", "นางสาว", "น.ส.", "น.ส", "นาง"]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break

    parts = [part for part in re.split(r"\s+", cleaned) if part]
    if not parts:
        return ""

    first_name = re.sub(r"[^\wก-๙]", "", parts[0])
    last_name_initial = ""
    if len(parts) > 1:
        last_name_initial = re.sub(r"[^\wก-๙]", "", parts[-1])[:1]

    normalized = f"{first_name}{last_name_initial}"
    normalized = re.sub(r"[\s\._,-]+", "", normalized)
    return normalized.lower()


def names_match_in_bank_format(api_name: str, reported_name: str) -> bool:
    api_normalized = normalize_bank_name(api_name)
    reported_normalized = normalize_bank_name(reported_name)
    if not api_normalized or not reported_normalized:
        return False
    return api_normalized == reported_normalized


def build_bank_mismatch_reason(bank_codes: list[str], bank_matches: list[bool]) -> str:
    mismatched_codes = [code for code, matched in zip(bank_codes, bank_matches) if code and not matched]
    if mismatched_codes:
        return (
            f"❌ Bank number mismatch: {', '.join(mismatched_codes)} "
            f"is not in ACCOUNT_MAPPING"
        )
    if bank_codes:
        return "❌ Bank number mismatch: the received bank account does not match the company bank account"
    return "❌ Bank number mismatch: unable to read the receiver bank number from the payload"


async def send_manual_review_message(
    bot,
    chat_id: str,
    reply_to_message_id: int,
    batch_id: str,
    reasons: list[str],
    footer_message: str,
):
    keyboard = get_approval_keyboard(batch_id)
    manual_reasons = reasons or ["• Manual slip review required"]
    alert_text = (
        "⚠️ Manual slip review required\n\n"
        + "\n".join(manual_reasons)
        + f"\n\n{footer_message}"
    )

    await bot.send_message(
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        text=alert_text,
        reply_markup=keyboard,
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    print(f"👉 [DEBUG] Received a message from the group with Chat ID: {chat_id}")
    msg_id = update.message.message_id
    caption = update.message.caption or ""
    
    photo = update.message.photo[-1]
    try:
        photo_file = await photo.get_file(
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )

        temp_path = f"temp_{msg_id}.jpg"

        await photo_file.download_to_drive(
            custom_path=temp_path,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )

    except TimedOut:
        await update.message.reply_text(
            "⚠️ Telegram connection timed out. Please send the image again."
        )
        return

    except NetworkError:
        await update.message.reply_text(
            "⚠️ Unable to connect to Telegram. Please try again."
        )
        return


    qr_data_list = read_qr_code(temp_path)
    
    # 👇 เพิ่มส่วนปริ้นท์ Payload ของ QR Code ลง Terminal ตรงนี้
    if qr_data_list:
        print(f"📸 [DEBUG] Detected {len(qr_data_list)} QR code(s):")
        for idx, qr in enumerate(qr_data_list, 1):
            print(f"   QR Code {idx} -> Payload: {qr}")
    else:
        print("📸 [DEBUG] No QR code detected in this image")
    # 👆 ----------------------------------------------------
    
    if qr_data_list:
        with SessionLocal() as db:
            # โยน qr_data_list ทั้งก้อนให้ระบบประมวลผล
            status, txn = process_incoming_slip(db, qr_data_list, chat_id, msg_id, caption)
            
            if status == "duplicate":
                await update.message.reply_text(
                    "⚠️ Duplicate: At least one slip in this image has already been used."
                )
            
            elif status == "pending":
                print(f"⏳ Received a group of {len(qr_data_list)} slip(s)... Waiting for a match.")
            
            elif status == "matched":
                print(f"🎉 Match found! Verifying {len(qr_data_list)} slip(s) via API...")
                
                target_chat_id = txn.g_user_chat_id if txn.g_user_chat_id else chat_id
                target_msg_id = int(txn.g_user_msg_id) if txn.g_user_msg_id else msg_id
                
                # ตัวแปรสำหรับรวมข้อมูล
                total_api_amount = 0.0
                all_senders = []
                all_receivers = []
                all_bank_codes = []
                all_bank_matches = []
                api_success = True
                error_msg = ""
                user_error_msg = "" # 👈 ตัวแปรสำหรับรับข้อความภาษาไทย
                
                # 🔄 ยิง API ตรวจสอบทีละใบและบวกยอดรวมกัน
                for qr in qr_data_list:
                    api_result = verify_slip(qr)
                    if api_result["success"]:
                        total_api_amount += api_result["amount"]
                        all_senders.append(api_result["sender"])
                        all_receivers.append(api_result["receiver"])
                        all_bank_codes.append(api_result.get("receiver_bank_code", ""))
                        all_bank_matches.append(api_result.get("receiver_bank_matches", False))
                        # ค้นหา UsedQR ใบนี้ แล้วยัด JSON ใส่เข้าไป
                        used_qr = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
                        if used_qr:
                            used_qr.api_raw_data = json.dumps(api_result["raw_data"], ensure_ascii=False)
                        
                    else:
                        api_success = False
                        error_msg = api_result.get("error", "UNKNOWN_ERROR")
                        # ดึงข้อความแจ้งเตือนภาษาไทยที่ส่งมาจาก verify_slip
                        user_error_msg = api_result.get("user_message", f"⚠️ Slip verification system error ({error_msg})")
                        break # ถ้าพังใบเดียว ให้ถือว่าล่มทั้งก้อนเลย
                
                if api_success:
                    chat_amount = txn.chat_amount
                    sender_names_str = ", ".join(all_senders) 
                    receiver_names_str = ", ".join(all_receivers)
                    chat_name = txn.chat_fullname or ""
                    
                    # บันทึกยอดรวมและชื่อรวมลง DB
                    txn.api_total_amount = total_api_amount
                    txn.sender_names = sender_names_str
                    txn.receiver_names = receiver_names_str
                    txn.receiver_account = receiver_names_str  # ♻️ ใช้ค่า mapped เต็มแทน chat_bank
                    
                    sender_matches = True
                    if chat_name and chat_name.strip() != "-":
                        sender_matches = all(
                            names_match_in_bank_format(sender, chat_name)
                            for sender in all_senders
                            if sender and sender.strip()
                        )
                    bank_matches = all(all_bank_matches) if all_bank_matches else False
                    is_name_match = True if not chat_name or chat_name.strip() == "-" else sender_matches

                    normalized_api_names = [normalize_bank_name(sender) for sender in all_senders if sender and sender.strip()]
                    normalized_reported_name = normalize_bank_name(chat_name)
                    logger.info(
                        "Slip name check | api_names=%s | reported_name=%s | normalized_api_names=%s | normalized_reported_name=%s | name_match=%s | bank_match=%s | amount_match=%s",
                        sender_names_str,
                        chat_name,
                        normalized_api_names,
                        normalized_reported_name,
                        is_name_match,
                        bank_matches,
                        chat_amount == total_api_amount,
                    )
                    
                    verification_decision = determine_verification_action(
                        api_success=True,
                        amount_matches=(chat_amount == total_api_amount),
                        name_matches=is_name_match,
                        bank_matches=bank_matches,
                    )

                    if verification_decision == VerificationDecision.AUTO_RECEIVE:
                        txn.status = "Receive"
                        txn.api_total_amount = total_api_amount
                        txn.sender_names = sender_names_str
                        txn.receiver_names = receiver_names_str
                        add_audit_log(db, txn.batch_id, "api_verified_matched")
                        db.commit()

                        sheet_success, sheet_error_msg = append_to_sheet(txn)
                        if sheet_success:
                            add_audit_log(db, txn.batch_id, "sheet_saved")
                            db.commit()
                            logger.info(
                                "✅ Auto-Received: Batch=%s | Receiver(s): %s | Agent: %s | Amount: %s",
                                txn.batch_id[:15], receiver_names_str, txn.receiver_account or "-", total_api_amount
                            )
                            await context.bot.send_message(
                                chat_id=target_chat_id,
                                reply_to_message_id=target_msg_id,
                                text=(f"✅ Auto-Received: all {len(qr_data_list)} slip(s) matched the API verification.\n\n"
                                    f"Sender(s): {sender_names_str}\n"
                                    f"Agent: {txn.receiver_account or '-'}\n"
                                    f"Verified total: {total_api_amount}\n"
                                    f"Reported amount: {chat_amount}"),
                            )
                        else:
                            await context.bot.send_message(
                                chat_id=target_chat_id,
                                reply_to_message_id=target_msg_id,
                                text=(f"⚠️ Auto-receive was prepared, but saving to Google Sheets failed.\n\n"
                                    f"{sheet_error_msg}"),
                            )
                    else:
                        txn.status = "Reject"
                        add_audit_log(db, txn.batch_id, "auto_rejected_mismatch")
                        remove_transaction_and_qr_links(db, txn.batch_id)
                        
                        reject_reason = ""
                        if not bank_matches:
                            reject_reason += build_bank_mismatch_reason(all_bank_codes, all_bank_matches) + "\n"
                        if chat_amount != total_api_amount:
                            reject_reason += f"❌ Amount: Slip `{total_api_amount}` | Reported `{chat_amount}`\n"
                        if not is_name_match:
                            reject_reason += f"❌ Sender Name format mismatch: Slip `{sender_names_str}` | Reported `{chat_name}`\n"
                            
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=(f"❌ Auto-Rejected: Information Mismatch\n\n"
                                f"{reject_reason if reject_reason else '❌ Unknown mismatch reason'}\n"
                                f"(This slip group has been automatically rejected.)"),
                        )
                else:
                    add_audit_log(db, txn.batch_id, "manual_review_required")
                    db.commit()

                    # ใช้ manual flow เดียวกัน แต่เปลี่ยนเหตุผลเป็นกรณี API ใช้ไม่ได้
                    await send_manual_review_message(
                        context.bot,
                        target_chat_id,
                        target_msg_id,
                        txn.batch_id,
                        [f"• {user_error_msg}"],
                        "Please perform a manual review of all slips and click one of the buttons below to proceed.",
                    )
                    
    if os.path.exists(temp_path):
        os.remove(temp_path)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    callback_data = query.data or ""

    from database.models import Transaction
    from database.session import SessionLocal
    from database.crud import add_audit_log

    with SessionLocal() as db:
        if callback_data.startswith("bank_"):
            _, short_ref, bank_value = callback_data.split("_", 2)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Record not found.", show_alert=True)
                return

            txn.chat_bank = bank_value
            txn.receiver_account = bank_value
            txn.status = "Receive"
            add_audit_log(db, txn.batch_id, "manual_bank_selected")

            # หากกำลังถูกบันทึกหรือบันทึกแล้ว ให้แจ้งผู้ใช้และไม่ดำเนินการ
            if is_sheet_locked(db, txn.batch_id):
                await query.answer("This slip is already being saved or has been saved.", show_alert=True)
                await query.edit_message_text(
                    text=(f"Already saved / In progress\nSelected Bank: {bank_value}"),
                    reply_markup=None,
                )
                return

            # สร้าง lock ว่าเริ่มการบันทึกแล้ว และแก้ไขข้อความให้หายปุ่ม (แสดงสถานะ Saving)
            add_audit_log(db, txn.batch_id, "saving_started")
            await query.answer("Saving manual bank selection to Google Sheets...")
            await query.edit_message_text(
                text=(f"Saving to Google Sheets...\nSelected Bank: {bank_value}"),
                reply_markup=None,
            )

            sheet_success, sheet_error_msg = append_to_sheet(txn)
            if sheet_success:
                # บันทึก marker ว่าได้บันทึกลง sheet แล้ว
                add_audit_log(db, txn.batch_id, "sheet_saved")
                db.commit()
                await query.edit_message_text(
                    text=(f"✅ Manual Receive Completed\n"
                        f"Selected Bank: {bank_value}\n\n"
                        f"Saved to Google Sheets."),
                    reply_markup=None,
                )
            else:
                db.rollback()
                await query.edit_message_text(
                    text=(f"Failed to save to Google Sheet.\n\n{sheet_error_msg}"),
                    reply_markup=None,
                )
            return

        if callback_data.startswith("back_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if txn:
                await query.answer("Returning to approval options...")
                await query.edit_message_reply_markup(reply_markup=get_approval_keyboard(txn.batch_id))
            return

        action, short_ref = callback_data.split('_', 1)
        txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()

        if txn:
            if txn.status in ["Receive", "Reject"]:
                status_icon = "✅" if txn.status == "Receive" else "❌"
                await query.answer(f"Slip already {txn.status}.", show_alert=True)
                await query.edit_message_text(
                    text=(f"{status_icon} Slip already {txn.status}."),
                    reply_markup=None,
                )
                return

            if action == "receive":
                txn.status = "Receive"
                action_text = "✅ Receive"
                await query.answer("Please select a bank/account value before saving...")
                await query.edit_message_text(
                    text="Please choose the bank/account value for this slip:",
                    reply_markup=get_bank_selection_keyboard(txn.batch_id),
                )
                return
            elif action == "reject":
                txn.status = "Reject"
                action_text = "❌ Reject"

            # ตอบ Telegram ทันที ก่อนทำงานที่ใช้เวลานาน
            await query.answer("Saving data to Google Sheets...")

            # หากกำลังถูกบันทึกหรือบันทึกแล้ว ให้แจ้งผู้ใช้และไม่ดำเนินการ
            if is_sheet_locked(db, txn.batch_id):
                await query.answer("Slip already saved or in-progress!", show_alert=True)
                await query.edit_message_text(
                    text=(f"Slip already saved or in-progress!"),
                    reply_markup=None,
                )
                return

            # สร้าง lock ว่าเริ่มการบันทึกแล้ว และลบปุ่มออกเพื่อป้องกันการกดซ้ำ
            add_audit_log(db, txn.batch_id, "saving_started")
            await query.edit_message_text(
                text="Saving to Google Sheets...",
                reply_markup=None,
            )

            sheet_success, sheet_error_msg = append_to_sheet(txn)

            if sheet_success:
                # บันทึก marker ว่าได้บันทึกลง sheet แล้ว
                add_audit_log(db, txn.batch_id, "sheet_saved")
                db.commit()
                add_audit_log(db, txn.batch_id, f"admin_clicked_{action}")

                await query.edit_message_text(
                    text=(f"Completed successfully!\n"
                          f"Action: {action_text}\n"
                          f"Reference Group: {txn.batch_id[:15]}...\n"
                          f"(Status updated to {txn.status})\n\n"
                          f"Saved to Google Sheets."),
                    reply_markup=None,
                )
            else:
                db.rollback()
                short_err = str(sheet_error_msg)[:150]
                await query.edit_message_text(
                    text=(f"Failed to save to Google Sheet.\n\n"
                          f"{short_err}\n\n"
                          f"Please fix the file and try again."),
                    reply_markup=None,
                )

        else:
            await query.answer("Record not found.", show_alert=True)
            await query.edit_message_text(
                text=("Error: This record was not found in the system."),
            )