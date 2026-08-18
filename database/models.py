from sqlalchemy import Column, String, Float, DateTime, Integer
from database.session import Base
import datetime

# ─────────────────────────────────────────────────────────────────────────────
# ความยาวคอลัมน์ — ต้องเผื่อให้พอกับข้อมูลจริงที่ยาวที่สุดที่เข้ามาได้
#
# ⚠️ SQLite (ที่ใช้ตอนทดสอบ) ไม่บังคับความยาว แต่ Postgres (ของจริง) บังคับ
# ค่าที่ยาวเกินจะทำให้ INSERT ล้ม → สลิปใบนั้นหายไปเงียบๆ ไม่มีใครได้รับแจ้ง
# เทสต์บน SQLite จับไม่ได้ จึงมีเทสต์แยกที่เทียบความยาวกับ schema โดยตรง
# ─────────────────────────────────────────────────────────────────────────────
CAPTION_MAX = 1200      # Telegram ให้ caption ยาวได้ถึง 1024 เผื่อไว้อีกหน่อย
NAME_MAX = 255          # ชื่อที่ดึงมาจาก caption คือทั้งบรรทัด ยาวได้มาก
NAMES_LIST_MAX = 1000   # ชื่อผู้โอน/ผู้รับของหลายสลิปต่อกัน
WARNING_MAX = 600       # ข้อความเตือนมีบรรทัดจาก caption แปะอยู่ด้วย
ACTION_MAX = 200
TRANSFER_TIME_MAX = 120  # "0:18, 0:37, ..." — พอสำหรับสลิปหลายใบในรูปเดียว
QR_REF_MAX = 512        # QR แบบ EMV ยาวได้ถึงราวๆ นี้
RECEIVER_NOTE_MAX = 255  # เหตุผลของด่านบัญชีผู้รับ เขียนให้คนอ่านเข้าใจในบรรทัดเดียว

# ห้ามตัดให้สั้นลงเด็ดขาด เพราะ QR สองใบที่ขึ้นต้นเหมือนกันจะกลายเป็นใบเดียวกัน
# แล้วด่านกันสลิปซ้ำจะปฏิเสธสลิปที่ถูกต้อง


def clamp(value, limit: int):
    """ตัดค่าที่ยาวเกินคอลัมน์ทิ้ง ดีกว่าปล่อยให้ INSERT ล้มแล้วสลิปหาย

    ใช้กับฟิลด์ที่เป็นข้อความอ่านประกอบเท่านั้น ห้ามใช้กับค่าที่ต้องเทียบกันตรงๆ
    """
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit]

class Transaction(Base):
    __tablename__ = 'transactions'

    # เปลี่ยน Primary Key เป็น batch_id (รหัสกลุ่มของสลิปในรูปนั้นๆ)
    batch_id = Column(String(100), primary_key=True, index=True)
    category = Column(String(50))  # VIP_WE หรือ VIP_12

    # --- ข้อความที่กลุ่มส่งมา (1 กลุ่มส่งครั้งเดียว) ---
    chat_id = Column(String(50), nullable=True)
    msg_id = Column(String(50), nullable=True)
    raw_caption = Column(String(CAPTION_MAX), nullable=True)
    # ข้อความที่มีปุ่ม Receive/Reject ติดอยู่ ใช้ตามไปปิดปุ่มตอนสลิปถูกตัดสินแล้ว
    review_msg_id = Column(String(50), nullable=True)

    chat_user_id = Column(String(50), nullable=True)   # format แบบ "User : benz4455"
    chat_trans_id = Column(String(50), nullable=True)  # format แบบ "TRANS ID : 0000004"
    chat_fullname = Column(String(NAME_MAX), nullable=True) # format แบบ "FULL NAME : ..."

    chat_bank = Column(String(50), nullable=True)
    chat_amount = Column(Float, nullable=True)
    # ข้อความในแชทมีจุดที่เชื่อไม่ได้ (เช่น ตัวเลขบวกกันแล้วไม่ตรงกับผลรวมที่เขียนไว้)
    # มีค่านี้เมื่อไหร่ = ห้าม auto receive/reject ต้องให้คนตัดสิน
    caption_warning = Column(String(WARNING_MAX), nullable=True)

    status = Column(String(20), default="pending")

    # --- ข้อมูลจาก API (เปลี่ยนเป็นแบบรวมยอด) ---
    api_total_amount = Column(Float, nullable=True) # ยอดรวมทุกใบ
    sender_names = Column(String(NAMES_LIST_MAX), nullable=True) # ชื่อคนโอนทุกคนรวมกัน
    receiver_names = Column(String(NAMES_LIST_MAX), nullable=True) # ชื่อ/บัญชีผู้รับทุกใบรวมกัน
    receiver_account = Column(String(255), nullable=True)
    # เหตุผลของด่านบัญชีผู้รับตอนตรวจครั้งแรก เก็บไว้ให้ /recheck และ /status
    # เล่าเรื่องเดียวกับที่บอทตอบในกลุ่ม ไม่ใช่เดาใหม่จากค่าที่เหลืออยู่
    receiver_note = Column(String(RECEIVER_NOTE_MAX), nullable=True)

    # ── เวลาที่โอนเงินจริงตามสลิป (คนละอย่างกับ created_at ที่เป็นเวลาที่บอทได้รับรูป) ──
    # transfer_at      = เวลาโอนของใบแรกในชุด เก็บเป็น UTC แบบไม่มี tzinfo เหมือน created_at
    #                    มีไว้ให้ query/เรียงลำดับได้
    # transfer_time_text = ข้อความที่จะเขียนลงช่อง Time ในชีทตรงๆ เช่น "0:18" หรือ "0:18, 0:37"
    #                    ต้องเก็บแยก เพราะรูปเดียวมีได้หลายสลิป ซึ่ง datetime ช่องเดียวเก็บไม่ครบ
    # แถวเก่าที่บันทึกไว้ก่อนหน้านี้จะเป็น NULL ทั้งคู่ ดึงย้อนหลังได้จาก used_qrs.api_raw_data
    transfer_at = Column(DateTime, nullable=True)
    transfer_time_text = Column(String(TRANSFER_TIME_MAX), nullable=True)

    # ── การกันสลิปซ้ำสำหรับใบที่อ่าน QR ไม่ออก ──────────────────────────
    # photo_hash = sha256 ของไฟล์รูปที่โหลดมา ใช้เป็นตัวระบุแทน QR
    #   แฮชเนื้อไฟล์เอง ไม่ใช้ file_unique_id ของ Telegram เพราะรูปเดียวกัน
    #   ที่ส่งแบบรูปกับส่งแบบไฟล์ จะได้ file_unique_id คนละค่า ทั้งที่เป็นไฟล์เดียวกัน
    # qr_request_msg_id = ข้อความที่บอททวง QR ไว้ เก็บลง DB ไม่ใช่หน่วยความจำ
    #   เพราะสลิปจะค้างรอจนกว่าจะมีคนตอบ ถ้าบอทรีสตาร์ทแล้วลืม จะไม่มีทางไปต่อ
    photo_hash = Column(String(64), nullable=True)
    qr_request_msg_id = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.datetime.utcnow)

# ตารางใหม่: ใช้เช็คสลิปซ้ำแยกเป็นรายใบ
class UsedQR(Base):
    __tablename__ = 'used_qrs'
    qr_ref = Column(String(QR_REF_MAX), primary_key=True, index=True)
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
    action = Column(String(ACTION_MAX))
    actor = Column(String(100), nullable=True) # ใครเป็นคนกด (ว่าง = ระบบทำเอง)
    timestamp = Column(DateTime, default=datetime.datetime.utcnow)