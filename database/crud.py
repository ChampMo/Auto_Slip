from sqlalchemy import update
from sqlalchemy.orm import Session
from database.models import Approver, AuditLog, Transaction

# action ที่ถือว่า batch นี้ถูกล็อกไว้แล้ว (กำลังบันทึก / บันทึกเสร็จแล้ว)
SHEET_LOCK_ACTIONS = ['saving_started', 'sheet_saved']
# action ที่ปลดล็อก: สลิปที่เคยถูก Reject แล้วถูกส่งเข้ามาใหม่
SHEET_REOPEN_ACTION = 'batch_reopened'

# สถานะที่ถือว่ามีคนตัดสินไปแล้ว ห้ามใครมาเปลี่ยนทับ
DECIDED_STATUSES = ['Receive', 'Reject']


def get_approver_ids(db: Session) -> set:
    """user id ของคนที่ถูกเพิ่มไว้ใน DB (ยังไม่รวมเจ้าของจาก .env)"""
    return {row[0] for row in db.query(Approver.user_id).all()}


def list_approvers(db: Session) -> list:
    """รายชื่อทั้งหมดใน DB เรียงตามลำดับที่ถูกเพิ่ม"""
    return db.query(Approver).order_by(Approver.added_at).all()


def add_approver(db: Session, user_id: str, username: str, display_name: str, added_by: str) -> bool:
    """เพิ่มคนเข้ารายชื่อ คืน False ถ้ามีอยู่แล้ว"""
    user_id = str(user_id)
    if db.query(Approver).filter(Approver.user_id == user_id).first() is not None:
        return False

    db.add(Approver(
        user_id=user_id,
        username=username,
        display_name=display_name,
        added_by=added_by,
    ))
    add_audit_log(db, f"approver:{user_id}", "approver_added", actor=added_by)
    return True


def remove_approver(db: Session, user_id: str, removed_by: str) -> bool:
    """ถอดคนออกจากรายชื่อ คืน False ถ้าไม่มีอยู่แล้ว"""
    user_id = str(user_id)
    approver = db.query(Approver).filter(Approver.user_id == user_id).first()
    if approver is None:
        return False

    db.delete(approver)
    add_audit_log(db, f"approver:{user_id}", "approver_removed", actor=removed_by)
    return True


def get_audit_trail(db: Session, batch_id: str, limit: int = 6) -> list:
    """ประวัติล่าสุดของรายการนี้ เรียงจากเก่าไปใหม่"""
    rows = (
        db.query(AuditLog)
        .filter(AuditLog.qr_ref == batch_id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
        .all()
    )
    return list(reversed(rows))


def find_interrupted_batches(db: Session) -> list:
    """หารายการที่เริ่มบันทึกลงชีทแล้วแต่ไม่มีผลลัพธ์ตามมา (บอทดับกลางคัน)

    ดูจาก log ล่าสุดของแต่ละ batch ถ้าเป็น 'saving_started' แปลว่าค้างอยู่ตรงนั้น
    """
    interrupted = []
    batch_ids = [row[0] for row in db.query(AuditLog.qr_ref).distinct().all()]

    for batch_id in batch_ids:
        last_log = (
            db.query(AuditLog)
            .filter(
                AuditLog.qr_ref == batch_id,
                AuditLog.action.in_(SHEET_LOCK_ACTIONS + [SHEET_REOPEN_ACTION]),
            )
            .order_by(AuditLog.id.desc())
            .first()
        )
        if last_log is not None and last_log.action == "saving_started":
            interrupted.append(batch_id)

    return interrupted


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


def add_audit_log(db: Session, qr_ref: str, action: str, actor: str = None):
    """บันทึกประวัติการทำงาน (Log)

    actor = คนที่กดปุ่ม ถ้าไม่ระบุแปลว่าระบบทำเอง (auto receive/reject)
    """
    log = AuditLog(qr_ref=qr_ref, action=action, actor=actor)
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
