from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from services.easyslip import BANK_DROPDOWN_VALUES


def get_approval_keyboard(batch_id: str, with_duplicate: bool = False,
                          with_add_qr: bool = True, with_receive: bool = True,
                          with_retry: bool = False):
    # ใช้ batch_id เป็นตัวอ้างอิงแทน
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id

    top = [InlineKeyboardButton("❌ Reject", callback_data=f"reject_{short_ref}")]
    # ตอนที่ธนาคารยังยืนยันไม่เสร็จ ไม่ควรมี Receive ให้กด เพราะการรอคือคำตอบที่ถูก
    # แต่ตอน EasySlip ล่มยาวหรือโควตาหมด ต้องเหลือทางให้คนตัดสินเอง ไม่งั้นธุรกิจหยุด
    if with_receive:
        top.append(InlineKeyboardButton("✅ Receive", callback_data=f"receive_{short_ref}"))
    keyboard = [top]

    if with_retry:
        keyboard.append([
            InlineKeyboardButton("🔄 Check with the bank again",
                                 callback_data=f"retry_{short_ref}")
        ])
    # โผล่เฉพาะตอนที่ระบบสงสัยว่าซ้ำ ไม่ขึ้นทุกใบ เพราะปุ่มที่ไม่ค่อยได้ใช้
    # จะกลายเป็นปุ่มที่คนกดพลาด และ "ซ้ำ" กับ "ปฏิเสธ" คนละความหมายกันในบัญชี
    if with_duplicate:
        keyboard.append([
            InlineKeyboardButton("♻️ Duplicate — already recorded",
                                 callback_data=f"dup_{short_ref}")
        ])
    # บางทีในรูปมี QR มากกว่าที่ระบบอ่านเจอ ซึ่งไม่มีทางตรวจจับได้เอง
    # จึงต้องมีทางให้คนบอกระบบว่า "ยังมีอีกใบ" แทนที่จะต้องส่งสลิปเข้ามาใหม่ทั้งชุด
    if with_add_qr:
        keyboard.append([
            InlineKeyboardButton("➕ Add another QR",
                                 callback_data=f"addqr_{short_ref}")
        ])
    return InlineKeyboardMarkup(keyboard)


def get_add_qr_keyboard(batch_id: str):
    """ปุ่มเดี่ยวสำหรับใบที่ถูกปฏิเสธเพราะยอดไม่ตรง

    ยอดไม่ตรงคือลายเซ็นของ 'มีสลิปที่ระบบมองไม่เห็น' จึงควรกดต่อได้ทันที
    ไม่ต้องพิมพ์ /recheck ก่อน
    """
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("➕ Add another QR", callback_data=f"addqr_{short_ref}")
    ]])


def get_bank_selection_keyboard(batch_id: str):
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id
    bank_values = BANK_DROPDOWN_VALUES

    buttons = [
        InlineKeyboardButton(value, callback_data=f"bank_{short_ref}_{value}")
        for value in bank_values
    ]

    keyboard = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    keyboard.append([
        InlineKeyboardButton("⬅️ Back", callback_data=f"back_{short_ref}")
    ])
    return InlineKeyboardMarkup(keyboard)

def get_qr_help_keyboard(batch_id: str):
    """ปุ่มใต้ข้อความทวง QR — ไว้ใช้ตอน QR บนสลิปเสียจนอ่านไม่ได้จริงๆ

    แยกออกมาจากปุ่ม Receive/Reject เพราะขั้นตอนนี้ยังไม่ใช่การตัดสินรับหรือไม่รับ
    แค่ยอมรับว่าใบนี้ตรวจกับธนาคารไม่ได้ แล้วส่งต่อให้คนตรวจเอง
    """
    short_ref = batch_id[:40] if len(batch_id) > 40 else batch_id
    keyboard = [[
        InlineKeyboardButton("🚫 QR on this slip is unreadable",
                             callback_data=f"noqr_{short_ref}")
    ]]
    return InlineKeyboardMarkup(keyboard)
