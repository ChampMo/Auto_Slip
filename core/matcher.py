import hashlib
from sqlalchemy.orm import Session

from core.captions import extract_data_from_caption
from core.config import config
from database.models import Transaction, UsedQR
from database.crud import add_audit_log, SHEET_REOPEN_ACTION


# 1 กลุ่ม = 1 category และแต่ละกลุ่มจะส่งสลิปใบหนึ่งมาเพียงรอบเดียว
GROUP_CATEGORY = {
    str(config.VIP_WE_CHAT_ID): "VIP_WE",
    str(config.VIP_12_CHAT_ID): "VIP_12",
}

# สถานะที่ถือว่า "จบไปแล้วแบบไม่ได้บันทึก" จึงยอมให้ส่งสลิปใบเดิมเข้ามาใหม่ได้
# interrupted = บอทดับระหว่างกำลังบันทึกลงชีท (กู้คืนตอนบูตครั้งถัดไป)
REJECTED_STATUSES = {"reject", "rejected", "interrupted"}


def generate_batch_id(qr_list: list):
    """นำรหัส QR ทุกใบในรูปมาเรียงต่อกันแล้วเข้ารหัสเป็น ID กลุ่ม"""
    combined = "".join(sorted(qr_list))
    return hashlib.md5(combined.encode('utf-8')).hexdigest()


def generate_no_qr_batch_id(chat_id: str, msg_id: str):
    """รูปที่อ่าน QR ไม่ออก ไม่มีรหัสสลิปให้ใช้ จึงอ้างอิงจากข้อความที่ส่งมาแทน"""
    return hashlib.md5(f"no_qr:{chat_id}:{msg_id}".encode('utf-8')).hexdigest()


def _is_batch_rejected(db: Session, batch_id: str) -> bool:
    """batch ที่โดน Reject ไปแล้ว ให้ถือว่าปลดล็อค เอาสลิปมาส่งใหม่ได้"""
    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
    if not txn:
        return True
    return str(txn.status).lower() in REJECTED_STATUSES


def _fill_transaction(txn: Transaction, chat_id: str, msg_id: str, caption: str, extracted: dict):
    txn.chat_id = chat_id
    txn.msg_id = str(msg_id)
    txn.raw_caption = caption
    txn.chat_user_id = extracted["user_id"]
    txn.chat_trans_id = extracted["trans_id"]
    txn.chat_fullname = extracted["fullname"]
    txn.chat_amount = extracted["amount"]
    # เช่น "100 + 100 = 300" — บวกไม่ลง ต้องให้คนมาดู ห้ามตัดสินเอง
    txn.caption_warning = extracted["amount_note"]


def process_incoming_slip(db: Session, qr_list: list, chat_id: str, msg_id: str, caption: str):
    """เช็คสลิปซ้ำแล้วบันทึกรายการใหม่

    คืนค่าเป็น (status, txn) โดย status คือ
      - "unknown_group": ไม่ใช่กลุ่มที่ลงทะเบียนไว้ ให้ข้ามไป
      - "duplicate": สลิปใบนี้เคยถูกใช้ไปแล้ว
      - "new": รับเข้าระบบแล้ว พร้อมส่งไปตรวจกับ API ต่อ
    """
    chat_id_str = str(chat_id)

    category = GROUP_CATEGORY.get(chat_id_str)
    if category is None:
        return "unknown_group", None

    # อ่าน QR ไม่ออกก็ยังรับเข้าระบบ แล้วให้แอดมินตัดสินจากข้อมูลในแชทแทน
    batch_id = generate_batch_id(qr_list) if qr_list else generate_no_qr_batch_id(chat_id_str, msg_id)
    extracted = extract_data_from_caption(caption)

    # 🛑 เช็คซ้ำระดับแยกใบ (สลิปใบเดิมถูกมัดรวมมาใหม่ หรือส่งข้ามกลุ่มมา)
    for qr in qr_list:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
        if used and used.batch_id != batch_id and not _is_batch_rejected(db, used.batch_id):
            add_audit_log(db, batch_id, f"duplicate_qr_found_{qr}")
            return "duplicate", None

    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()

    if txn:
        # 🛑 รูปเดิมเป๊ะถูกส่งซ้ำ (ยกเว้นเคยโดน Reject ให้ส่งมาแก้ตัวได้)
        if str(txn.status).lower() not in REJECTED_STATUSES:
            add_audit_log(db, batch_id, "duplicate_batch_resent")
            return "duplicate", txn

        txn.category = category
        _fill_transaction(txn, chat_id_str, msg_id, caption, extracted)
        txn.status = "pending"
        # ปลดล็อกการบันทึกลงชีทของรอบก่อนที่โดน Reject ไป
        add_audit_log(db, batch_id, SHEET_REOPEN_ACTION)
    else:
        txn = Transaction(batch_id=batch_id, category=category, status="pending")
        _fill_transaction(txn, chat_id_str, msg_id, caption, extracted)
        db.add(txn)

    # จองสลิปทุกใบให้เป็นของ batch นี้ (รองรับ QR เก่าที่เคยถูก Reject แล้วเอามาส่งใหม่)
    for qr in qr_list:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
        if used:
            used.batch_id = batch_id
        else:
            db.add(UsedQR(qr_ref=qr, batch_id=batch_id))

    db.commit()
    return "new", txn
