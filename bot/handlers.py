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
                        
                        # 👇 --- โค้ดที่เพิ่มใหม่: ค้นหา UsedQR ใบนี้ แล้วยัด JSON ใส่เข้าไป ---
                        used_qr = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
                        if used_qr:
                            # แปลง Dictionary เป็นข้อความ JSON และรองรับภาษาไทย
                            used_qr.api_raw_data = json.dumps(api_result["raw_data"], ensure_ascii=False)
                        # 👆 ----------------------------------------------------
                        
                    else:
                        api_success = False
                        error_msg = api_result["error"]
                        break # ถ้าพังใบเดียว ให้ถือว่าล่มทั้งก้อนเลย
                
                if api_success:
                    chat_amount = txn.chat_amount
                    sender_names_str = ", ".join(all_senders) 
                    chat_name = txn.chat_fullname or ""
                    
                    # บันทึกยอดรวมและชื่อรวมลง DB
                    txn.api_total_amount = total_api_amount
                    txn.sender_names = sender_names_str
                    
                    # 🔍 ---------------------------------------------
                    # ลอจิกตรวจสอบชื่อ (เช็คเฉพาะชื่อจริง ไม่เอาคำนำหน้าและนามสกุล)
                    is_name_match = True
                    if chat_name and chat_name.strip() != "-":
                        clean_name = chat_name.strip()
                        # ตัดคำนำหน้าชื่อที่พบบ่อยออก
                        for p in ["นาย", "นางสาว", "น.ส.", "น.ส. ", "นาง"]:
                            if clean_name.startswith(p):
                                clean_name = clean_name[len(p):].strip()
                                break
                        
                        # ดึงเฉพาะคำแรกสุด (ชื่อจริง)
                        first_name = clean_name.split()[0] if clean_name else ""
                        
                        # ถ้าชื่อจริงจากแชท ไม่มีในชื่อที่โอนมาเลย -> ปฏิเสธ
                        if first_name and first_name not in sender_names_str:
                            is_name_match = False
                    # ------------------------------------------------
                    
                    # เปรียบเทียบทั้ง ยอดเงิน และ ชื่อผู้โอน
                    if chat_amount == total_api_amount and is_name_match:
                        add_audit_log(db, txn.batch_id, "api_verified_matched")
                        db.commit()

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
                        add_audit_log(db, txn.batch_id, "auto_rejected_mismatch")
                        db.commit()
                        
                        # สร้างข้อความแจ้งเตือนว่าผิดที่จุดไหน
                        reject_reason = ""
                        if chat_amount != total_api_amount:
                            reject_reason += f"❌ ยอดเงิน: ในสลิป `{total_api_amount}` | แจ้งมา `{chat_amount}`\n"
                        if not is_name_match:
                            reject_reason += f"❌ ชื่อผู้โอน: ในสลิป `{sender_names_str}` | แจ้งมา `{chat_name}`\n"
                            
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=f"❌ **Auto-Rejected: ข้อมูลไม่ตรงกัน!**\n"
                                f"{reject_reason}\n"
                                f"*(ระบบปฏิเสธสลิปชุดนี้อัตโนมัติ)*",
                            parse_mode="Markdown"
                        )
                else:
                    # 💡 แยกแยะข้อความแจ้งเตือนตามประเภท Error
                    keyboard = get_approval_keyboard(txn.batch_id)
                    
                    if error_msg == "QUOTA_EXCEEDED":
                        alert_text = (
                            f"🚨 **แจ้งเตือนด่วน: โควต้า API ตรวจสอบสลิปหมด!** 🚨\n"
                            f"แพ็กเกจ EasySlip ของคุณถูกใช้งานครบกำหนดแล้ว\n\n"
                            f"👉 *ระหว่างนี้โปรดตรวจสอบยอดเงินในสลิปด้วยตัวเอง (Manual) และกดปุ่มด้านล่างเพื่อบันทึกยอดครับ*\n\n"
                            f"💡 *แนะนำ: กรุณาต่ออายุแพ็กเกจ EasySlip เพื่อให้ระบบตรวจจับอัตโนมัติทำงานต่อ*"
                        )
                    else:
                        alert_text = (
                            f"⚠️ **ระบบตรวจสอบสลิปขัดข้อง**\n"
                            f"สาเหตุ: `{error_msg}`\n\n"
                            f"👉 *โปรดตรวจสอบสลิปทั้งหมดด้วยตัวเอง และกดปุ่มด้านล่างครับ*"
                        )

                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        reply_to_message_id=target_msg_id,
                        text=alert_text, 
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
                    
    if os.path.exists(temp_path):
        os.remove(temp_path)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    # ❌ เอา await query.answer() บรรทัดนี้ออก เพื่อเก็บสิทธิ์ไว้ใช้โชว์ Popup ตอนที่พังครับ
    
    action, short_ref = query.data.split('_', 1)
    
    from database.models import Transaction
    from database.session import SessionLocal
    from database.crud import add_audit_log
    
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
        
        if txn:
            # 🛡️ UX ที่ 1: ป้องกันแอดมินกดเบิ้ล หรือกดปุ่มจากสลิปที่ตรวจไปแล้ว
            if txn.status in ["Receive", "Reject"]:
                await query.answer(f"สลิปนี้ถูก {txn.status} ไปแล้วครับ!", show_alert=True)
                # แอบลบปุ่มทิ้งให้ด้วย เพราะสลิปนี้จบงานไปแล้ว
                await query.edit_message_reply_markup(reply_markup=None)
                return

            # เตรียมข้อความและสถานะชั่วคราว
            if action == "receive":
                txn.status = "Receive"
                action_text = "✅ อนุมัติรับยอด (Receive)"
            elif action == "reject":
                txn.status = "Reject"
                action_text = "❌ ปฏิเสธ (Reject)"
                
            # 🚀 ลองส่งข้อมูลขึ้น Google Sheets ก่อน
            # หมายเหตุ: ต้องมั่นใจว่าใน gsheets.py คืนค่าเป็น (True, "") หรือ (False, "error") ตามที่ทำไว้รอบก่อน
            sheet_success, sheet_error_msg = append_to_sheet(txn)
            
            if sheet_success:
                # ✅ ถ้าลง Sheet ผ่าน ค่อย Save ข้อมูลลง Database จริงๆ
                db.commit()
                add_audit_log(db, txn.batch_id, f"admin_clicked_{action}")
                
                # ตอบรับว่ากดปุ่มสำเร็จ (ไฟกะพริบที่ปุ่มดับลง)
                await query.answer("บันทึกข้อมูลเรียบร้อย!") 
                
                await query.edit_message_text(
                    text=f"📌 **ดำเนินการเรียบร้อย!**\n"
                         f"แอดมินกดปุ่ม: {action_text}\n"
                         f"Ref Group: `{txn.batch_id[:15]}...`\n"
                         f"*(สถานะอัปเดตเป็น {txn.status})*\n"
                         f"📊 บันทึกลง Sheet สำเร็จ", 
                    parse_mode="Markdown"
                )
            else:
                # ❌ 🛡️ UX ที่ 2 & 3: Sheet พัง ต้อง Rollback และเด้ง Popup
                db.rollback() # คืนสถานะใน DB กลับเป็น Pending
                
                # Telegram จำกัดข้อความ Popup ไม่เกิน 200 ตัวอักษร เราต้องหั่นข้อความป้องกันบอทแครช
                short_err = str(sheet_error_msg)[:150]
                
                # เด้ง Popup แจ้งเตือนกลางจอ และไม่ลบปุ่ม เพื่อให้แอดมินกลับมากดใหม่ได้
                await query.answer(
                    text=f"⚠️ บันทึกลง Sheet ไม่สำเร็จ!\n\n{short_err}\n\n👉 โปรดแก้ไฟล์แล้วกดใหม่อีกครั้ง",
                    show_alert=True
                )
        else:
            await query.answer("ไม่พบข้อมูลในระบบ", show_alert=True)
            await query.edit_message_text(text="⚠️ **เกิดข้อผิดพลาด:** ไม่พบข้อมูลนี้ในระบบ", parse_mode="Markdown")