from sqlalchemy import update
from sqlalchemy.orm import Session
from database.models import AuditLog, Transaction

# action ที่ถือว่า batch นี้ถูกล็อกไว้แล้ว (กำลังบันทึก / บันทึกเสร็จแล้ว)
SHEET_LOCK_ACTIONS = ['saving_started', 'sheet_saved']
# action ที่ปลดล็อก: สลิปที่เคยถูก Reject แล้วถูกส่งเข้ามาใหม่
SHEET_REOPEN_ACTION = 'batch_reopened'

# สถานะที่ถือว่ามีคนตัดสินไปแล้ว ห้ามใครมาเปลี่ยนทับ
DECIDED_STATUSES = ['Receive', 'Reject']


def claim_transaction(db: Session, batch_id: str, new_status: str) -> bool:
    """จองสิทธิ์ตัดสินรายการนี้ คืน True เฉพาะคนที่จองสำเร็จ

    ใช้ UPDATE ... WHERE สถานะยังไม่ถูกตัดสิน เพื่อให้ฐานข้อมูลเป็นคนชี้ขาดว่าใครกดก่อน
    การอ่านสถานะมาเช็คแล้วค่อยเขียนทีหลังมีช่องว่างให้อีกคนแทรกเข้ามาได้
    """
    result = db.execute(
        update(Transaction)
        .where(Transaction.batch_id == batch_id)
        .where(Transaction.status.notin_(DECIDED_STATUSES))
        .values(status=new_status)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def add_audit_log(db: Session, qr_ref: str, action: str):
    """บันทึกประวัติการทำงาน (Log)"""
    log = AuditLog(qr_ref=qr_ref, action=action)
    db.add(log)
    db.commit()


def is_sheet_locked(db: Session, batch_id: str) -> bool:
    """ตรวจสอบว่า batch นี้กำลังถูกบันทึกหรือบันทึกไปแล้ว

    ดูจาก log ล่าสุดเท่านั้น เพราะสลิปที่เคยถูก Reject แล้วส่งกลับเข้ามาใหม่
    จะมี log 'batch_reopened' คั่นไว้ ทำให้บันทึกรอบใหม่ได้ตามปกติ
    """
    last_log = (
        db.query(AuditLog)
        .filter(
            AuditLog.qr_ref == batch_id,
            AuditLog.action.in_(SHEET_LOCK_ACTIONS + [SHEET_REOPEN_ACTION]),
        )
        .order_by(AuditLog.id.desc())
        .first()
    )
    return last_log is not None and last_log.action in SHEET_LOCK_ACTIONS
