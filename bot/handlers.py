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

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    msg_id = update.message.message_id
    caption = update.message.caption or ""
    
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    temp_path = f"temp_{msg_id}.jpg"
    await photo_file.download_to_drive(temp_path)
    
    qr_data_list = read_qr_code(temp_path)
    
    if qr_data_list:
        with SessionLocal() as db:
            # โยน qr_data_list ทั้งก้อนให้ระบบประมวลผล
            status, txn = process_incoming_slip(db, qr_data_list, chat_id, msg_id, caption)
            
            if status == "duplicate":
                await update.message.reply_text("⚠️ `[Duplicate]` มีสลิปอย่างน้อย 1 ใบในรูปนี้ถูกนำไปใช้งานแล้ว!", parse_mode="Markdown")
            
            elif status == "pending":
                print(f"⏳ รับกลุ่มสลิป {len(qr_data_list)} ใบ... รอคู่")
            
            elif status == "matched":
                print(f"🎉 จับคู่สำเร็จ! กำลังยิง API ตรวจสอบสลิป {len(qr_data_list)} ใบ...")
                
                target_chat_id = txn.g_user_chat_id if txn.g_user_chat_id else chat_id
                target_msg_id = int(txn.g_user_msg_id) if txn.g_user_msg_id else msg_id
                
                # ตัวแปรสำหรับรวมข้อมูล
                total_api_amount = 0.0
                all_senders = []
                api_success = True
                error_msg = ""
                
                # 🔄 ยิง API ตรวจสอบทีละใบและบวกยอดรวมกัน
                for qr in qr_data_list:
                    api_result = verify_slip(qr)
                    if api_result["success"]:
                        total_api_amount += api_result["amount"]
                        all_senders.append(api_result["sender"])
                    else:
                        api_success = False
                        error_msg = api_result["error"]
                        break # ถ้าพังใบเดียว ให้ถือว่าล่มทั้งก้อนเลย
                
                if api_success:
                    chat_amount = txn.chat_amount
                    sender_names_str = ", ".join(all_senders) # รวมชื่อเป็น: นาย A, นาย B
                    
                    # บันทึกยอดรวมและชื่อรวมลง DB
                    txn.api_total_amount = total_api_amount
                    txn.sender_names = sender_names_str
                    
                    # เทียบยอดรวม API กับยอดในแชท
                    if chat_amount == total_api_amount:
                        db.commit()
                        add_audit_log(db, txn.batch_id, "api_verified_matched")
                        
                        keyboard = get_approval_keyboard(txn.batch_id)
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=f"✅ **ตรวจสอบผ่านครบ {len(qr_data_list)} ใบ!**\n"
                                 f"ผู้โอน: `{sender_names_str}`\n"
                                 f"ยอดรวมจริง: `{total_api_amount}`\n"
                                 f"ยอดในแชท: `{chat_amount}`\n\n"
                                 f"👉 *โปรดตรวจสอบและกดปุ่ม ✅ Receive*", 
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )
                    else:
                        txn.status = "Reject"
                        db.commit()
                        add_audit_log(db, txn.batch_id, "auto_rejected_mismatch")
                        
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=f"❌ **Auto-Rejected: ยอดรวมไม่ตรงกัน!**\n"
                                 f"ยอดรวมสลิปจริง: `{total_api_amount}`\n"
                                 f"ยอดที่พิมพ์แจ้ง: `{chat_amount}`\n\n"
                                 f"*(ระบบปฏิเสธสลิปชุดนี้อัตโนมัติ)*",
                            parse_mode="Markdown"
                        )
                else:
                    keyboard = get_approval_keyboard(txn.batch_id)
                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        reply_to_message_id=target_msg_id,
                        text=f"⚠️ **ระบบตรวจสอบสลิปขัดข้อง**\n"
                             f"สาเหตุ: `{error_msg}`\n\n"
                             f"👉 *โปรดตรวจสอบสลิปทั้งหมดด้วยตัวเอง และกดปุ่มด้านล่างครับ*", 
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    
    if os.path.exists(temp_path):
        os.remove(temp_path)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, short_ref = query.data.split('_', 1)
    
    from database.models import Transaction
    from database.session import SessionLocal
    from database.crud import add_audit_log
    
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
        
        if txn:
            if action == "receive":
                txn.status = "Receive"
                action_text = "✅ อนุมัติรับยอด (Receive)"
            elif action == "reject":
                txn.status = "Reject"
                action_text = "❌ ปฏิเสธ (Reject)"
                
            db.commit()
            add_audit_log(db, txn.batch_id, f"admin_clicked_{action}")
            
            # 🚀 ส่งข้อมูลขึ้น Google Sheets
            sheet_status = append_to_sheet(txn)
            sheet_msg = "✅ บันทึกลง Google Sheets แล้ว" if sheet_status else "⚠️ บันทึกลง Sheet ไม่สำเร็จ"
            
            await query.edit_message_text(
                text=f"📌 **ดำเนินการเรียบร้อย!**\n"
                     f"แอดมินกดปุ่ม: {action_text}\n"
                     f"Ref Group: `{txn.batch_id[:15]}...`\n"
                     f"*(สถานะอัปเดตเป็น {txn.status})*\n"
                     f"📊 {sheet_msg}", 
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(text="⚠️ **เกิดข้อผิดพลาด:** ไม่พบข้อมูลนี้ในระบบ", parse_mode="Markdown")