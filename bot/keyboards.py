from telegram import InlineKeyboardButton, InlineKeyboardMarkup

def get_approval_keyboard(batch_id: str):
    # ใช้ batch_id เป็นตัวอ้างอิงแทน
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id
    
    keyboard = [
        [
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_{short_ref}"),
            InlineKeyboardButton("✅ Receive", callback_data=f"receive_{short_ref}")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)