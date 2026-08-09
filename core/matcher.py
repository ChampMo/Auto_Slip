import hashlib
import logging

from sqlalchemy.orm import Session

from core.captions import extract_data_from_caption
from core.config import config
from database.models import (
    CAPTION_MAX,
    NAME_MAX,
    WARNING_MAX,
    Transaction,
    UsedQR,
    clamp,
)
from database.crud import add_audit_log, SHEET_REOPEN_ACTION

logger = logging.getLogger(__name__)


# 1 กลุ่ม = 1 category และแต่ละกลุ่มจะส่งสลิปใบหนึ่งมาเพียงรอบเดียว
GROUP_CATEGORY = {
    str(config.VIP_WE_CHAT_ID): "VIP_WE",
    str(config.VIP_12_CHAT_ID): "VIP_12",
}

# สถานะที่ถือว่า "จบไปแล้วแบบไม่ได้บันทึก" จึงยอมให้ส่งสลิปใบเดิมเข้ามาใหม่ได้
# interrupted = บอทดับระหว่างกำลังบันทึกลงชีท (กู้คืนตอนบูตครั้งถัดไป)
REJECTED_STATUSES = {"reject", "rejected", "interrupted"}

# รอ QR จากคนส่ง — ยังไม่ถือเป็นรายการที่ตัดสินได้ ต้องได้ QR หรือมีคนยืนยันว่า QR เสียก่อน
NEEDS_QR_STATUS = "needs_qr"

# สถานะที่ยอมให้เอาชุดเดิมกลับเข้ากระบวนการได้ใหม่ ไม่ใช่ตีเป็นของซ้ำ
REOPENABLE_STATUSES = REJECTED_STATUSES | {NEEDS_QR_STATUS}


def generate_batch_id(qr_list: list):
    """นำรหัส QR ทุกใบในรูปมาเรียงต่อกันแล้วเข้ารหัสเป็น ID กลุ่ม"""
    combined = "".join(sorted(qr_list))
    return hashlib.md5(combined.encode('utf-8')).hexdigest()


def generate_no_qr_batch_id(chat_id: str, msg_id: str):
    """รูปที่อ่าน QR ไม่ออก ไม่มีรหัสสลิปให้ใช้ จึงอ้างอิงจากข้อความที่ส่งมาแทน"""
    return hashlib.md5(f"no_qr:{chat_id}:{msg_id}".encode('utf-8')).hexdigest()


# แฮชรูปเก็บในตารางเดียวกับ QR โดยใส่คำนำหน้าไว้ไม่ให้ชนกัน
# ใช้ตารางเดิมเพื่อให้ได้กลไกที่มีอยู่แล้วมาทั้งชุด — เช็คซ้ำ ปลดล็อกเมื่อใบเดิมถูก Reject
# และการชี้กลับไปว่าใบแรกคือ batch ไหน
PHOTO_KEY_PREFIX = "photo:"


def photo_key(photo_hash: str) -> str:
    return f"{PHOTO_KEY_PREFIX}{photo_hash}"


def _is_batch_rejected(db: Session, batch_id: str) -> bool:
    """batch ที่โดน Reject ไปแล้ว ให้ถือว่าปลดล็อค เอาสลิปมาส่งใหม่ได้"""
    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
    if not txn:
        return True
    return str(txn.status).lower() in REJECTED_STATUSES


def _fill_transaction(txn: Transaction, chat_id: str, msg_id: str, caption: str, extracted: dict):
    # ตัดค่าที่ยาวเกินคอลัมน์ก่อนเสมอ — ถ้าปล่อยไป Postgres จะปฏิเสธทั้งแถว
    # แล้วสลิปใบนั้นจะหายไปเงียบๆ โดยไม่มีใครในกลุ่มรู้
    txn.chat_id = chat_id
    txn.msg_id = str(msg_id)
    txn.raw_caption = clamp(caption, CAPTION_MAX)
    txn.chat_user_id = clamp(extracted["user_id"], 50)
    txn.chat_trans_id = clamp(extracted["trans_id"], 50)
    txn.chat_fullname = clamp(extracted["fullname"], NAME_MAX)
    txn.chat_amount = extracted["amount"]
    # เช่น "100 + 100 = 300" — บวกไม่ลง ต้องให้คนมาดู ห้ามตัดสินเอง
    txn.caption_warning = clamp(extracted["amount_note"], WARNING_MAX)


def process_incoming_slip(db: Session, qr_list: list, chat_id: str, msg_id: str, caption: str,
                          photo_hashes: list = None, allow_without_qr: bool = False,
                          force_batch_id: str = None):
    """เช็คสลิปซ้ำแล้วบันทึกรายการใหม่

    คืนค่าเป็น (status, txn) โดย status คือ
      - "unknown_group": ไม่ใช่กลุ่มที่ลงทะเบียนไว้ ให้ข้ามไป
      - "duplicate": สลิปใบนี้เคยถูกใช้ไปแล้ว
      - "needs_qr": อ่าน QR ไม่ออก ต้องขอ QR จากคนส่งก่อนถึงจะไปต่อได้
      - "new": รับเข้าระบบแล้ว พร้อมส่งไปตรวจกับ API ต่อ

    ทุกใบต้องมี QR เสมอ เพราะ QR คือสิ่งเดียวที่เอาไปยืนยันกับธนาคารได้
    และเป็นตัวกันสลิปซ้ำที่เชื่อถือได้ที่สุด ยกเว้นมีคนยืนยันว่า QR เสียจริง
    (allow_without_qr) ซึ่งตอนนั้นจะกันซ้ำด้วยแฮชของไฟล์รูปแทน
    """
    chat_id_str = str(chat_id)
    photo_hashes = [h for h in (photo_hashes or []) if h]

    category = GROUP_CATEGORY.get(chat_id_str)
    if category is None:
        return "unknown_group", None

    # เติม QR ให้ใบเดิมต้องคงรหัสชุดเดิมไว้ ไม่งั้นรหัสจะเปลี่ยนไปตาม QR ที่เพิ่มเข้ามา
    # แล้ว QR ดวงเดิมที่จองไว้กับชุดเก่าจะกลายเป็น 'ซ้ำ' ทั้งที่เป็นชุดเดียวกัน
    batch_id = force_batch_id or (
        generate_batch_id(qr_list) if qr_list else generate_no_qr_batch_id(chat_id_str, msg_id)
    )
    extracted = extract_data_from_caption(caption)

    # 🛑 เช็คซ้ำจากแฮชรูป — ใช้ได้แม้อ่าน QR ไม่ออก ไฟล์เดียวกันคือสลิปใบเดียวกัน
    for key in [photo_key(h) for h in photo_hashes]:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == key).first()
        if used and used.batch_id != batch_id and not _is_batch_rejected(db, used.batch_id):
            logger.info(
                "Duplicate photo | batch_id=%s | already used by=%s | key=%.24s...",
                batch_id, used.batch_id, key,
            )
            add_audit_log(db, used.batch_id, "duplicate_photo_found")
            original = db.query(Transaction).filter(
                Transaction.batch_id == used.batch_id).first()
            db.commit()
            return "duplicate", original

    # 🛑 เช็คซ้ำระดับแยกใบ (สลิปใบเดิมถูกมัดรวมมาใหม่ หรือส่งข้ามกลุ่มมา)
    for qr in qr_list:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
        if used and used.batch_id != batch_id and not _is_batch_rejected(db, used.batch_id):
            # ห้ามเอา QR payload มาต่อเป็นชื่อ action — payload ยาวเกินคอลัมน์ได้
            # แล้วการบันทึกจะล้มตรงด่านกันสลิปซ้ำพอดี ซึ่งเป็นด่านสำคัญที่สุด
            # อยากรู้ว่าเป็น QR ใบไหน ดูได้จากตาราง used_qrs ที่โยง qr -> batch อยู่แล้ว
            logger.info(
                "Duplicate QR | batch_id=%s | already used by=%s | qr=%.40s...",
                batch_id, used.batch_id, qr,
            )
            # บันทึกไว้ใต้ "ใบแรก" ไม่ใช่ batch ของชุดที่ส่งมาใหม่
            # เพราะชุดใหม่ไม่ถูกสร้างเป็นแถวใน transactions ประวัติจะลอยไปหาไม่เจอ
            # ส่วนใบแรกมีแถวอยู่จริง จึงตามดูได้ด้วย /status
            add_audit_log(db, used.batch_id, "duplicate_qr_found")
            # คืนรายการเดิมไปด้วย เพื่อบอกคนส่งได้ว่าใบก่อนหน้าจบยังไงแล้ว
            original = db.query(Transaction).filter(
                Transaction.batch_id == used.batch_id).first()
            return "duplicate", original

    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()

    if txn:
        # ใบที่ยังรอ QR อยู่ ให้ทวงซ้ำได้ ไม่ใช่ตีเป็นของซ้ำ
        # (เกิดตอนบอทรีสตาร์ทแล้วคนส่งรูปเดิมเข้ามาใหม่)
        if str(txn.status).lower() == NEEDS_QR_STATUS and not qr_list and not allow_without_qr:
            _fill_transaction(txn, chat_id_str, msg_id, caption, extracted)
            txn.photo_hash = photo_hashes[0] if photo_hashes else txn.photo_hash
            db.commit()
            return "needs_qr", txn

        # 🛑 รูปเดิมเป๊ะถูกส่งซ้ำ (ยกเว้นเคยโดน Reject ให้ส่งมาแก้ตัวได้)
        #
        # ใบที่รอ QR อยู่ก็ต้องผ่านตรงนี้ไปได้ด้วย เพราะการ "ส่ง QR ที่ขาดมาเติม"
        # คือการเอาชุดเดิมมาตรวจใหม่ ไม่ใช่การส่งสลิปซ้ำ
        # (เคยพลาดตรงนี้: เติม QR แล้วบอทตอบว่า "สลิปซ้ำ" แล้วใบนั้นค้างถาวร)
        if str(txn.status).lower() not in REOPENABLE_STATUSES:
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

    txn.photo_hash = photo_hashes[0] if photo_hashes else txn.photo_hash

    # ไม่มี QR และยังไม่มีใครยืนยันว่า QR เสีย = ยังตรวจกับธนาคารไม่ได้ ต้องขอ QR ก่อน
    # ไม่จองแฮชรูปตรงนี้ เพราะใบนี้ยังไม่ใช่รายการจริง ถ้าจองไว้แล้วคนส่งรูปเดิมมาใหม่
    # จะโดนตีเป็นของซ้ำแล้วไปต่อไม่ได้เลย
    if not qr_list and not allow_without_qr:
        txn.status = NEEDS_QR_STATUS
        db.commit()
        return "needs_qr", txn

    # จองสลิปทุกใบให้เป็นของ batch นี้ (รองรับ QR เก่าที่เคยถูก Reject แล้วเอามาส่งใหม่)
    for key in [photo_key(h) for h in photo_hashes]:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == key).first()
        if used:
            used.batch_id = batch_id
        else:
            db.add(UsedQR(qr_ref=key, batch_id=batch_id))

    for qr in qr_list:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
        if used:
            used.batch_id = batch_id
        else:
            db.add(UsedQR(qr_ref=qr, batch_id=batch_id))

    db.commit()
    return "new", txn
