from sqlalchemy.orm import Session
from database.models import Transaction, AuditLog

def get_transaction(db: Session, qr_ref: str):
    """ค้นหา Transaction จาก QR Code"""
    return db.query(Transaction).filter(Transaction.qr_ref == qr_ref).first()

def add_audit_log(db: Session, qr_ref: str, action: str):
    """บันทึกประวัติการทำงาน (Log)"""
    log = AuditLog(qr_ref=qr_ref, action=action)
    db.add(log)
    db.commit()