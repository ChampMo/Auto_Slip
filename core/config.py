import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY")
    DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
    GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

    # Chat ID ของ 2 กลุ่มที่ส่งสลิปเข้ามา (ตั้งค่าใน .env ได้)
    VIP_WE_CHAT_ID = os.getenv("VIP_WE_CHAT_ID", "-1004418034373")
    VIP_12_CHAT_ID = os.getenv("VIP_12_CHAT_ID", "-5153291438")

    # กลุ่มที่เปิดใช้ Topics จะมี chat id เดียวกันทุกหัวข้อ ต้องระบุหมายเลข topic
    # ถึงจะแยกได้ว่าให้บอททำงานเฉพาะหัวข้อไหน — เว้นว่าง = รับทุกหัวข้อเหมือนเดิม
    # หาเลขได้จากลิงก์ของข้อความในหัวข้อนั้น: t.me/c/<chat>/<topic>/<message>
    VIP_WE_TOPIC_ID = (os.getenv("VIP_WE_TOPIC_ID") or "").strip()
    VIP_12_TOPIC_ID = (os.getenv("VIP_12_TOPIC_ID") or "").strip()

    # ── ตัวฟังข้อความจากบอทตัวอื่น (ไม่ตั้ง = ปิดไว้ ระบบทำงานปกติทุกอย่าง) ──
    # ขอ api_id/api_hash ที่ my.telegram.org ด้วยเบอร์ของบัญชีที่จะใช้เป็นตัวฟัง
    # RELAY_SESSION ต้องชี้ไปที่ไฟล์ที่ mount ออกมานอกคอนเทนเนอร์
    # ไม่งั้นไฟล์จะหายทุกครั้งที่ rebuild แล้วต้องกรอก OTP ใหม่ทุกรอบ
    # รับทั้งชื่อที่มี RELAY_ นำหน้าและชื่อสั้นที่ my.telegram.org ใช้เรียก
    # จะได้ไม่ต้องมาไล่เปลี่ยนชื่อคีย์ทั้งในเครื่องและบนเซิร์ฟเวอร์ให้พลาดกันอีก
    RELAY_API_ID = (os.getenv("RELAY_API_ID") or os.getenv("API_ID") or "").strip()
    RELAY_API_HASH = (os.getenv("RELAY_API_HASH") or os.getenv("API_HASH") or "").strip()
    RELAY_SESSION = (os.getenv("RELAY_SESSION") or "data/relay").strip()
    # username ของบอทที่ยอมรับ คั่นด้วย , (เว้นว่าง = รับจากบอทตัวไหนก็ได้ในกลุ่ม)
    RELAY_SOURCE_BOTS = frozenset(
        part.strip().lower()
        for part in (os.getenv("RELAY_SOURCE_BOTS") or "").split(",")
        if part.strip()
    )

    # user id ของคนที่กดปุ่ม Receive/Reject ได้ คั่นด้วย , เช่น "123456789,987654321"
    # ไม่ตั้งค่า = ไม่มีใครกดได้เลย (ตั้งใจให้เป็นแบบนี้ จะได้ไม่เผลอเปิดสิทธิ์ทิ้งไว้)
    SLIP_APPROVER_IDS = frozenset(
        part.strip()
        for part in os.getenv("SLIP_APPROVER_IDS", "").split(",")
        if part.strip()
    )


config = Config()