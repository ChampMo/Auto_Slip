from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_approval_keyboard(qr_ref: str):
    # Telegram บังคับว่า Callback data ห้ามยาวเกิน 64 ตัวอักษร
    # เราจึงตัดเอาแค่รหัสสั้นๆ มาเป็นตัวอ้างอิงเวลาแอดมินกดปุ่ม
    short_ref = qr_ref[:40] if len(qr_ref) > 40 else qr_ref
    
    keyboard = [
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{short_ref}"),
            InlineKeyboardButton("✅ Receive", callback_data=f"receive_{short_ref}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)