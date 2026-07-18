from sqlalchemy import Column, String, Float, DateTime, Integer, ForeignKey
from sqlalchemy.orm import declarative_base, relationship
import datetime

Base = declarative_base()

class Transaction(Base):
    __tablename__ = 'transactions'

    qr_ref = Column(String(100), primary_key=True, index=True)
    category = Column(String(50))
    
    # --- ฝั่งกลุ่ม User ID ---
    g_user_chat_id = Column(String(50), nullable=True)
    g_user_msg_id = Column(String(50), nullable=True)
    raw_user_caption = Column(String(500), nullable=True)  # เก็บข้อความดิบ: "Hi team, User : 65090500452..."
    chat_user_id = Column(String(50), nullable=True)       # เก็บสิ่งที่สกัดได้: "65090500452"
    
    # --- ฝั่งกลุ่ม Trans ID ---
    g_trans_chat_id = Column(String(50), nullable=True)
    g_trans_msg_id = Column(String(50), nullable=True)
    raw_trans_caption = Column(String(500), nullable=True) # เก็บข้อความดิบ: "TRANS ID : 0000001..."
    chat_trans_id = Column(String(50), nullable=True)      # เก็บสิ่งที่สกัดได้: "0000001"
    chat_fullname = Column(String(100), nullable=True)     # เก็บสิ่งที่สกัดได้: "มณฑล สุขจินดา"
    
    chat_amount = Column(Float, nullable=True) # ยอดเงินที่พิมพ์ในแชท (ใช้เทียบยอด)
    
    status = Column(String(20), default="pending")
    
    # --- ฝั่งข้อมูลจาก API ---
    api_amount = Column(Float, nullable=True)
    sender_name = Column(String(100), nullable=True)
    receiver_name = Column(String(100), nullable=True)
    api_raw_response = Column(String, nullable=True) # เก็บ JSON ดิบจาก API เผื่อไว้ดีบั๊ก
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    
    logs = relationship("AuditLog", back_populates="transaction")

class AuditLog(Base):
    __tablename__ = 'audit_logs'

    log_id = Column(Integer, primary_key=True, autoincrement=True)
    qr_ref = Column(String(100), ForeignKey('transactions.qr_ref'))
    
    action = Column(String(255))
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    transaction = relationship("Transaction", back_populates="logs")