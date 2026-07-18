import os
from telegram import Update
from telegram.ext import ContextTypes
from core.scanner import read_qr_code
from bot.keyboards import get_approval_keyboard
from database.session import SessionLocal # เพิ่มบรรทัดนี้
from core.matcher import process_incoming_slip # เพิ่มบรรทัดนี้
from services.easyslip import verify_slip
from database.crud import add_audit_log


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    msg_id = update.message.message_id
    caption = update.message.caption or ""
    
    photo = update.message.photo[-1]
    photo_file = await photo.get_file()
    temp_path = f"temp_{msg_id}.jpg"
    await photo_file.download_to_drive(temp_path)
    
    qr_data = read_qr_code(temp_path)
    
    if qr_data:
        # เปิดการเชื่อมต่อ Database 1 ครั้ง ต่อ 1 รูปภาพ
        with SessionLocal() as db:
            status, txn = process_incoming_slip(db, qr_data, chat_id, msg_id, caption)
            
            if status == "duplicate":
                # แจ้งเตือนกลับไปยัง "กลุ่มที่ส่งมาซ้ำ" โดยตรง (ตามที่ขอครับ)
                await update.message.reply_text("⚠️ `[Duplicate]` สลิปนี้ถูกใช้งานไปแล้ว!", parse_mode="Markdown")
            
            elif status == "pending":
                print(f"⏳ รับสลิป {qr_data[:10]}... รอคู่จากอีกกลุ่ม")
            
            elif status == "matched":
                print(f"🎉 จับคู่สำเร็จ! กำลังยิง API ตรวจสอบสลิป...")
                
                # 1. ยิง API ตรวจสอบสลิป
                api_result = verify_slip(qr_data)
                
                # ดึง chat_id และ msg_id ของกลุ่ม User ออกมา 
                # (ใส่ Fallback ป้องกัน Error กรณีลืมพิมพ์คำว่า User)
                target_chat_id = txn.g_user_chat_id if txn.g_user_chat_id else chat_id
                target_msg_id = int(txn.g_user_msg_id) if txn.g_user_msg_id else msg_id
                
                if api_result["success"]:
                    api_amount = api_result["amount"]
                    sender_name = api_result["sender"]
                    chat_amount = txn.chat_amount
                    
                    # บันทึกข้อมูล API ลง Database
                    txn.api_amount = api_amount
                    txn.sender_name = sender_name
                    
                    # 2. เปรียบเทียบยอดเงิน (Auto-Reject Logic)
                    if chat_amount == api_amount:
                        db.commit()
                        add_audit_log(db, qr_data, "api_verified_matched")
                        
                        keyboard = get_approval_keyboard(qr_data)
                        # แจ้งเตือนเมื่อสำเร็จ พร้อมบอกให้แอดมินรู้ว่าต้องทำอะไรต่อ
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=f"✅ **ตรวจสอบสลิปสำเร็จ ข้อมูลถูกต้อง!**\n"
                                    f"Ref: `{qr_data[:15]}...`\n"
                                    f"ผู้โอน: `{sender_name}`\n"
                                    f"ยอดโอนจริง/ในแชท: `{api_amount}`\n\n"
                                    f"👉 *โปรดตรวจสอบความถูกต้องอีกครั้ง และกดปุ่ม ✅ Receive เพื่อยืนยันรับยอดครับ*", 
                            reply_markup=keyboard,
                            parse_mode="Markdown"
                        )
                    else:
                        txn.status = "Reject"
                        db.commit()
                        add_audit_log(db, qr_data, "auto_rejected_mismatch")
                        
                        # แจ้งยอดไม่ตรงไปที่กลุ่ม User เท่านั้น (Auto-Reject ไม่มีปุ่ม)
                        await context.bot.send_message(
                            chat_id=target_chat_id,
                            reply_to_message_id=target_msg_id,
                            text=f"❌ **Auto-Rejected: ยอดเงินไม่ตรงกัน!**\n"
                                    f"ยอดในสลิปจริง: `{api_amount}`\n"
                                    f"ยอดที่พิมพ์แจ้ง: `{chat_amount}`\n\n"
                                    f"*(ระบบปฏิเสธสลิปนี้อัตโนมัติแล้ว)*",
                            parse_mode="Markdown"
                        )
                else:
                    # กรณีตรวจสอบสลิปไม่สำเร็จ (API Error) -> เรียกปุ่มมาให้แอดมินตรวจเอง
                    keyboard = get_approval_keyboard(qr_data)
                    await context.bot.send_message(
                        chat_id=target_chat_id,
                        reply_to_message_id=target_msg_id,
                        text=f"⚠️ **ระบบตรวจสอบสลิปอัตโนมัติไม่สำเร็จ**\n"
                                f"สาเหตุ: `{api_result['error']}`\n\n"
                                f"👉 *โปรดตรวจสอบสลิปและยอดเงินด้วยตัวเอง และกดเลือกปุ่มด้านล่างเพื่อดำเนินการต่อครับ*", 
                        reply_markup=keyboard,
                        parse_mode="Markdown"
                    )
    
    if os.path.exists(temp_path):
        os.remove(temp_path)



async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # ตอบรับการกดปุ่ม
    
    # แยกแอคชัน (reject/receive) และรหัสสลิปแบบสั้นออกมา
    action, short_ref = query.data.split('_', 1)
    
    # นำเข้า Transaction Model เพื่อใช้ค้นหา
    from database.models import Transaction
    
    # เปิดการเชื่อมต่อ Database
    with SessionLocal() as db:
        # ค้นหาสลิปจาก Database 
        # (เนื่องจากตอนสร้างปุ่มเราตัดความยาว QR เหลือ 40 ตัวอักษร เราจึงใช้ .startswith() ในการค้นหา)
        txn = db.query(Transaction).filter(Transaction.qr_ref.startswith(short_ref)).first()
        
        if txn:
            # อัปเดตสถานะตามปุ่มที่กด
            if action == "receive":
                txn.status = "Receive"
                action_text = "✅ อนุมัติรับยอด (Receive)"
            elif action == "reject":
                txn.status = "Reject"
                action_text = "❌ ปฏิเสธ (Reject)"
                
            # เซฟลง Database
            db.commit()
            add_audit_log(db, txn.qr_ref, f"admin_clicked_{action}")
            
            # อัปเดตข้อความใน Telegram
            await query.edit_message_text(
                text=f"📌 **ดำเนินการเรียบร้อย!**\n"
                        f"แอดมินกดปุ่ม: {action_text}\n"
                        f"Ref: `{txn.qr_ref[:20]}...`\n"
                        f"*(สถานะใน Database อัปเดตเป็น {txn.status} แล้ว)*", 
                parse_mode="Markdown"
            )
        else:
            # กรณีบั๊ก หรือสลิปโดนลบออกจาก DB ไปแล้ว
            await query.edit_message_text(
                text="⚠️ **เกิดข้อผิดพลาด:** ไม่พบข้อมูลสลิปนี้ในฐานข้อมูล", 
                parse_mode="Markdown"
            )