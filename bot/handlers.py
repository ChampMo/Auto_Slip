import os
import logging
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
            "⚠️ การเชื่อมต่อกับ Telegram หมดเวลา กรุณาส่งรูปอีกครั้ง"
        )
        return

    except NetworkError:
        await update.message.reply_text(
            "⚠️ ไม่สามารถเชื่อมต่อกับ Telegram ได้ กรุณาลองใหม่อีกครั้ง"
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
                    
                    # 🔍 ลอจิกตรวจสอบชื่อ (เช็คเฉพาะชื่อจริง ไม่เอาคำนำหน้าและนามสกุล)
                    is_name_match = True
                    if chat_name and chat_name.strip() != "-":
                        clean_name = chat_name.strip()
                        for p in ["นาย", "นางสาว", "น.ส.", "น.ส. ", "นาง"]:
                            if clean_name.startswith(p):
                                clean_name = clean_name[len(p):].strip()
                                break
                        
                        first_name = clean_name.split()[0] if clean_name else ""
                        
                        if first_name and first_name not in sender_names_str:
                            is_name_match = False
                    
                    verification_decision = determine_verification_action(
                        api_success=True,
                        amount_matches=(chat_amount == total_api_amount),
                        name_matches=is_name_match,
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
                                      f"Receiver: {receiver_names_str}\n"
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
                        if chat_amount != total_api_amount:
                            reject_reason += f"❌ Amount: Slip `{total_api_amount}` | Reported `{chat_amount}`\n"
                        if not is_name_match:
                            reject_reason += f"❌ Sender Name: Slip `{sender_names_str}` | Reported `{chat_name}`\n"
                            
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=(f"❌ Auto-Rejected: Information Mismatch\n\n"
                                  f"{reject_reason}\n"
                                  f"(This slip group has been automatically rejected.)"),
                        )
                else:
                    # แจ้งเตือน User ด้วยข้อความภาษาไทยสวยๆ จากไฟล์ easyslip.py
                    keyboard = get_approval_keyboard(txn.batch_id)
                    
                    alert_text = (
                        f"{user_error_msg}\n\n"
                        f"Please perform a manual review of all slips and click one of the buttons below to proceed."
                    )

                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        reply_to_message_id=target_msg_id,
                        text=alert_text,
                        reply_markup=keyboard,
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