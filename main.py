from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from pyzbar.pyzbar import decode
from PIL import Image
import sqlite3
import os

BOT_TOKEN = "8832140368:AAGrdZn6cnT95kO17_6NVbrk408dTXJSQPU"

def read_qr_code(image_path):
    try:
        img = Image.open(image_path)
        decoded_objects = decode(img)
        if decoded_objects:
            return decoded_objects[0].data.decode('utf-8') 
        return None
    except Exception as e:
        print(f"อ่าน QR Code ไม่สำเร็จ: {e}")
        return None

def process_slip_match(qr_data, chat_id, caption, file_id):
    conn = sqlite3.connect('slips.db')
    c = conn.cursor()
    
    # อัปเดตตารางให้รองรับการเก็บ chat_id และ file_id
    c.execute('''CREATE TABLE IF NOT EXISTS slip_data
                 (ref_id TEXT PRIMARY KEY, 
                  group1_data TEXT, group1_chat_id TEXT, 
                  group2_data TEXT, group2_chat_id TEXT, 
                  status TEXT, file_id TEXT)''')
    
    c.execute("SELECT * FROM slip_data WHERE ref_id=?", (qr_data,))
    row = c.fetchone()
    
    is_match = False
    combined_info = {}
    
    if row is None:
        c.execute("INSERT INTO slip_data (ref_id, group1_data, group1_chat_id, group2_data, group2_chat_id, status, file_id) VALUES (?, ?, ?, ?, ?, ?, ?)", 
                  (qr_data, caption, str(chat_id), "", "", "pending", file_id))
    else:
        if row[5] == "pending": # ตรวจสอบที่คอลัมน์ status
            c.execute("UPDATE slip_data SET group2_data=?, group2_chat_id=?, status=? WHERE ref_id=?", 
                      (caption, str(chat_id), "matched", qr_data))
            is_match = True
            combined_info = {
                'qr_ref': qr_data,
                'caption_1': row[1],
                'chat_1': row[2],
                'caption_2': caption,
                'chat_2': str(chat_id),
                'file_id': row[6] # ใช้ file_id ของรูปแรกที่บันทึกไว้
            }
            
    conn.commit()
    conn.close()
    return is_match, combined_info

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # เก็บ file_id ของรูป เพื่อให้บอทส่งรูปเดิมได้เร็วขึ้น
    photo = update.message.photo[-1]
    file_id = photo.file_id
    photo_file = await photo.get_file()
    
    temp_path = f"temp_slip_{update.message.message_id}.jpg"
    await photo_file.download_to_drive(temp_path)
    
    caption = update.message.caption or ""
    chat_id = update.message.chat_id
    
    # --- เพิ่ม Log ตรงนี้ ---
    print(f"ได้รับรูปจากกลุ่ม: {chat_id}")
    print(f"แคปชั่น: {caption}")
    
    qr_data = read_qr_code(temp_path)
    
    if qr_data:
        # --- เพิ่ม Log ตรงนี้ ---
        print("สแกน QR Code สำเร็จ! กำลังตรวจสอบข้อมูล...")
        
        # ส่ง file_id เข้าไปเก็บใน DB ด้วย
        is_match, combined_info = process_slip_match(qr_data, chat_id, caption, file_id)
        
        if is_match:
            # --- เพิ่ม Log ตรงนี้ ---
            print("จับคู่สลิปสำเร็จ! กำลังส่งข้อความอนุมัติ...")
            # --- กำหนดเงื่อนไขส่งไปเฉพาะกลุ่มที่มีคำว่า "User" ---
            target_chat_id = chat_id # ค่าเริ่มต้น
            if "User" in combined_info['caption_1'] or "user" in combined_info['caption_1'].lower():
                target_chat_id = combined_info['chat_1']
            elif "User" in combined_info['caption_2'] or "user" in combined_info['caption_2'].lower():
                target_chat_id = combined_info['chat_2']

            text_to_show = (
                "🔍 **พบสลิปที่ตรงกันจากทั้ง 2 กลุ่ม!**\n"
                "------------------\n"
                f"**ข้อมูลชุดที่ 1:**\n{combined_info['caption_1']}\n"
                "------------------\n"
                f"**ข้อมูลชุดที่ 2:**\n{combined_info['caption_2']}\n"
                "------------------\n"
                "กรุณาตรวจสอบและดำเนินการ:"
            )
            
            short_ref = combined_info['qr_ref'][:20] 
            
            keyboard = [
                [
                    InlineKeyboardButton("❌ Reject", callback_data=f"reject_{short_ref}"),
                    InlineKeyboardButton("⚠️ Duplicate", callback_data=f"duplicate_{short_ref}"),
                    InlineKeyboardButton("✅ Receive", callback_data=f"receive_{short_ref}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # --- เปลี่ยนจาก send_message เป็น send_photo ---
            await context.bot.send_photo(
                chat_id=target_chat_id, 
                photo=combined_info['file_id'], 
                caption=text_to_show, 
                reply_markup=reply_markup, 
                parse_mode='Markdown'
            )
            
    if os.path.exists(temp_path):
        os.remove(temp_path)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, ref_id = query.data.split('_', 1)
    
    # เนื่องจากข้อความเป็นแบบมีรูปภาพ ต้องใช้ edit_message_caption แทน edit_message_text
    if action == "receive":
        await query.edit_message_caption(caption=f"✅ รายการนี้ถูกรับเข้าระบบเรียบร้อยแล้ว (รอยืนยันลง Sheets)")
    elif action == "reject":
        await query.edit_message_caption(caption=f"❌ ปฏิเสธรายการนี้เรียบร้อยแล้ว")
    elif action == "duplicate":
        await query.edit_message_caption(caption=f"⚠️ แจ้งเตือน: รายการนี้ถูกระบุว่าซ้ำ!")

    # ลบข้อมูลออกจากฐานข้อมูลเพื่อเทสซ้ำได้
    try:
        conn = sqlite3.connect('slips.db')
        c = conn.cursor()
        c.execute("DELETE FROM slip_data WHERE ref_id LIKE ?", (f"{ref_id}%",))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"เกิดข้อผิดพลาดในการลบข้อมูล: {e}")

if __name__ == '__main__':
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    print("Bot กำลังทำงาน...")
    app.run_polling()