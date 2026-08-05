from sqlalchemy import Column, String, Float, DateTime, Integer
from database.session import Base
import datetime

class Transaction(Base):
    __tablename__ = 'transactions'

    # เปลี่ยน Primary Key เป็น batch_id (รหัสกลุ่มของสลิปในรูปนั้นๆ)
    batch_id = Column(String(100), primary_key=True, index=True)
    category = Column(String(50))  # VIP_WE หรือ VIP_12

    # --- ข้อความที่กลุ่มส่งมา (1 กลุ่มส่งครั้งเดียว) ---
    chat_id = Column(String(50), nullable=True)
    msg_id = Column(String(50), nullable=True)
    raw_caption = Column(String(500), nullable=True)
    # ข้อความที่มีปุ่ม Receive/Reject ติดอยู่ ใช้ตามไปปิดปุ่มตอนสลิปถูกตัดสินแล้ว
    review_msg_id = Column(String(50), nullable=True)

    chat_user_id = Column(String(50), nullable=True)   # format แบบ "User : benz4455"
    chat_trans_id = Column(String(50), nullable=True)  # format แบบ "TRANS ID : 0000004"
    chat_fullname = Column(String(100), nullable=True) # format แบบ "FULL NAME : ..."

    chat_bank = Column(String(50), nullable=True)
    chat_amount = Column(Float, nullable=True)
    # ข้อความในแชทมีจุดที่เชื่อไม่ได้ (เช่น ตัวเลขบวกกันแล้วไม่ตรงกับผลรวมที่เขียนไว้)
    # มีค่านี้เมื่อไหร่ = ห้าม auto receive/reject ต้องให้คนตัดสิน
    caption_warning = Column(String(200), nullable=True)

    status = Column(String(20), default="pending")

    # --- ข้อมูลจาก API (เปลี่ยนเป็นแบบรวมยอด) ---
    api_total_amount = Column(Float, nullable=True) # ยอดรวมทุกใบ
    sender_names = Column(String(300), nullable=True) # ชื่อคนโอนทุกคนรวมกัน
    receiver_names = Column(String(300), nullable=True) # ชื่อ/บัญชีผู้รับทุกใบรวมกัน
    receiver_account = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# ตารางใหม่: ใช้เช็คสลิปซ้ำแยกเป็นรายใบ
class UsedQR(Base):
    __tablename__ = 'used_qrs'
    qr_ref = Column(String(100), primary_key=True, index=True)
    batch_id = Column(String(100)) # โยงไปหาว่าอยู่ใน Transaction กลุ่มไหน
    api_raw_data = Column(String, nullable=True) # เก็บเป็นก้อน JSON Text

class Approver(Base):
    """คนที่กดปุ่ม Receive/Reject ได้ (นอกเหนือจากเจ้าของที่ตั้งไว้ใน .env)"""
    __tablename__ = 'approvers'
    user_id = Column(String(50), primary_key=True, index=True)
    username = Column(String(100), nullable=True)      # เปลี่ยนได้ ใช้แค่แสดงผล
    display_name = Column(String(100), nullable=True)
    added_by = Column(String(100), nullable=True)
    added_at = Column(DateTime, default=datetime.datetime.utcnow)


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True, autoincrement=True)
    qr_ref = Column(String(100)) # ใช้เก็บ batch_id แทนในเวอร์ชันนี้
    action = Column(String(100))
    actor = Column(String(100), nullable=True) # ใครเป็นคนกด (ว่าง = ระบบทำเอง)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)