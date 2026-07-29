from sqlalchemy import Column, String, Float, DateTime, Integer
from database.session import Base
import datetime

class Transaction(Base):
    __tablename__ = 'transactions'

    # เปลี่ยน Primary Key เป็น batch_id (รหัสกลุ่มของสลิปในรูปนั้นๆ)
    batch_id = Column(String(100), primary_key=True, index=True)
    category = Column(String(50))
    
    g_user_chat_id = Column(String(50), nullable=True)
    g_user_msg_id = Column(String(50), nullable=True)
    raw_user_caption = Column(String(500), nullable=True) 
    chat_user_id = Column(String(50), nullable=True)     
    
    g_trans_chat_id = Column(String(50), nullable=True)
    g_trans_msg_id = Column(String(50), nullable=True)
    raw_trans_caption = Column(String(500), nullable=True) 
    chat_trans_id = Column(String(50), nullable=True)      
    chat_fullname = Column(String(100), nullable=True)     
    
    chat_bank = Column(String(50), nullable=True)
    chat_amount = Column(Float, nullable=True) 
    
    status = Column(String(20), default="pending")
    
    # --- ข้อมูลจาก API (เปลี่ยนเป็นแบบรวมยอด) ---
    api_total_amount = Column(Float, nullable=True) # ยอดรวมทุกใบ
    sender_names = Column(String(300), nullable=True) # ชื่อคนโอนทุกคนรวมกัน
    receiver_account = Column(String(255), nullable=True)
    
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# ตารางใหม่: ใช้เช็คสลิปซ้ำแยกเป็นรายใบ
class UsedQR(Base):
    __tablename__ = 'used_qrs'
    qr_ref = Column(String(100), primary_key=True, index=True)
    batch_id = Column(String(100)) # โยงไปหาว่าอยู่ใน Transaction กลุ่มไหน
    api_raw_data = Column(String, nullable=True) # เก็บเป็นก้อน JSON Text

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    qr_ref = Column(String(100)) # ใช้เก็บ batch_id แทนในเวอร์ชันนี้
    action = Column(String(100))
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)