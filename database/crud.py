from sqlalchemy.orm import Session
from database.models import Transaction, AuditLog, UsedQR

def get_transaction(db: Session, qr_ref: str):
    """ค้นหา Transaction จาก QR Code"""
    return db.query(Transaction).filter(Transaction.qr_ref == qr_ref).first()

def add_audit_log(db: Session, qr_ref: str, action: str):
    """บันทึกประวัติการทำงาน (Log)"""
    log = AuditLog(qr_ref=qr_ref, action=action)
    db.add(log)
    db.commit()


def is_sheet_saved(db: Session, batch_id: str) -> bool:
    """ตรวจสอบว่าเคยบันทึกไปยัง Google Sheets สำหรับ batch นี้หรือไม่"""
    return db.query(AuditLog).filter(AuditLog.qr_ref == batch_id, AuditLog.action == 'sheet_saved').first() is not None


def remove_transaction_and_qr_links(db: Session, batch_id: str) -> None:
    """Remove a transaction and all UsedQR links for a rejected/mismatched batch so it can be resent."""
    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
    if txn:
        db.delete(txn)

    qr_links = db.query(UsedQR).filter(UsedQR.batch_id == batch_id).all()
    for qr_link in qr_links:
        db.delete(qr_link)

    db.commit()


def is_sheet_locked(db: Session, batch_id: str) -> bool:
    """ตรวจสอบว่า batch นี้กำลังถูกบันทึกหรือบันทึกแล้ว (lock)

    ใช้ action 'saving_started' เป็นตัวบ่งชี้ว่ามีการเริ่มกระบวนการบันทึก
    และ 'sheet_saved' แสดงว่าบันทึกเสร็จแล้ว
    """
    return db.query(AuditLog).filter(
        AuditLog.qr_ref == batch_id,
        AuditLog.action.in_(['saving_started', 'sheet_saved'])
    ).first() is not None