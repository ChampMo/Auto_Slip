import os
from telegram import Update
from telegram.ext import ContextTypes
from core.scanner import read_qr_code
from bot.keyboards import get_approval_keyboard
from database.session import SessionLocal
from core.matcher import process_incoming_slip
from services.easyslip import verify_slip
from database.crud import add_audit_log
from services.gsheets import append_to_sheet
import json
from database.models import UsedQR

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    print(f"👉 [DEBUG] Received a message from the group with Chat ID: {chat_id}")
    msg_id = update.message.message_id
    caption = update.message.caption or ""
    
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    temp_path = f"temp_{msg_id}.jpg"
    await photo_file.download_to_drive(temp_path)
    
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
                    "⚠️ `[Duplicate]` At least one slip in this image has already been used!",
                    parse_mode="Markdown"
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
                api_success = True
                error_msg = ""
                user_error_msg = "" # 👈 ตัวแปรสำหรับรับข้อความภาษาไทย
                
                # 🔄 ยิง API ตรวจสอบทีละใบและบวกยอดรวมกัน
                for qr in qr_data_list:
                    api_result = verify_slip(qr)
                    if api_result["success"]:
                        total_api_amount += api_result["amount"]
                        all_senders.append(api_result["sender"])
                        
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
                    chat_name = txn.chat_fullname or ""
                    
                    # บันทึกยอดรวมและชื่อรวมลง DB
                    txn.api_total_amount = total_api_amount
                    txn.sender_names = sender_names_str
                    
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
                    
                    # เปรียบเทียบทั้ง ยอดเงิน และ ชื่อผู้โอน
                    if chat_amount == total_api_amount and is_name_match:
                        add_audit_log(db, txn.batch_id, "api_verified_matched")
                        db.commit()

                        keyboard = get_approval_keyboard(txn.batch_id)
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                           text=f"✅ **All {len(qr_data_list)} Slip(s) Verified Successfully!**\n"
                                f"Sender: `{sender_names_str}`\n"
                                f"Verified Amount: `{total_api_amount}`\n"
                                f"Chat Amount: `{chat_amount}`\n\n"
                                f"👉 *Please review the information and click ✅ Receive.*",
                            reply_markup=keyboard,
                            # parse_mode="MarkdownV2"
                        )
                    else:
                        txn.status = "Reject"
                        add_audit_log(db, txn.batch_id, "auto_rejected_mismatch")
                        db.commit()
                        
                        reject_reason = ""
                        if chat_amount != total_api_amount:
                            reject_reason += f"❌ Amount: Slip `{total_api_amount}` | Reported `{chat_amount}`\n"
                        if not is_name_match:
                            reject_reason += f"❌ Sender Name: Slip `{sender_names_str}` | Reported `{chat_name}`\n"
                            
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=f"❌ **Auto-Rejected: Information Mismatch!**\n"
                                f"{reject_reason}\n"
                                f"*(This slip group has been automatically rejected.)*",
                            # parse_mode="MarkdownV2"
                        )
                else:
                    # แจ้งเตือน User ด้วยข้อความภาษาไทยสวยๆ จากไฟล์ easyslip.py
                    keyboard = get_approval_keyboard(txn.batch_id)
                    
                    alert_text = (
                        f"{user_error_msg}\n\n"
                        f"👉 *Please perform a manual review of all slips and click one of the buttons below to proceed.*"
                    )

                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        reply_to_message_id=target_msg_id,
                        text=alert_text, 
                        reply_markup=keyboard,
                        # parse_mode="MarkdownV2"
                    )
                    
    if os.path.exists(temp_path):
        os.remove(temp_path)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    action, short_ref = query.data.split('_', 1)

    from database.models import Transaction
    from database.session import SessionLocal
    from database.crud import add_audit_log

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()

        if txn:
            if txn.status in ["Receive", "Reject"]:
                await query.answer(f" Slip {txn.status} already!", show_alert=True)
                await query.edit_message_text(
                    text=f"📌 Slip {txn.status} already!",
                    reply_markup=None,
                )
                return

            if action == "receive":
                txn.status = "Receive"
                action_text = "✅ Receive"
            elif action == "reject":
                txn.status = "Reject"
                action_text = "❌ Reject"

            # ตอบ Telegram ทันที ก่อนทำงานที่ใช้เวลานาน
            await query.answer("Saving data to Google Sheets...")

            sheet_success, sheet_error_msg = append_to_sheet(txn)

            if sheet_success:
                db.commit()
                add_audit_log(db, txn.batch_id, f"admin_clicked_{action}")

                await query.edit_message_text(
                    text=f"📌 **Completed Successfully!**\n"
                        f"Action: {action_text}\n"
                        f"Reference Group: `{txn.batch_id[:15]}...`\n"
                        f"*(Status updated to {txn.status})*\n"
                        f"📊 Successfully saved to Google Sheets.",
                    reply_markup=None,
                    # parse_mode="MarkdownV2"
                )
            else:
                db.rollback()
                short_err = str(sheet_error_msg)[:150]

                await query.edit_message_text(
                    text=f"⚠️ Failed to save to Google Sheet!\n\n"
                        f"{short_err}\n\n"
                        f"👉 Please fix the file and try again",
                    reply_markup=None,
                )

        else:
            await query.answer("Record not found.", show_alert=True)
            await query.edit_message_text(
                text="⚠️ **Error:** This record was not found in the system.",
                # parse_mode="Markdown"
            )