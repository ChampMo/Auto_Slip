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

    # user id ของคนที่กดปุ่ม Receive/Reject ได้ คั่นด้วย , เช่น "123456789,987654321"
    # ไม่ตั้งค่า = ไม่มีใครกดได้เลย (ตั้งใจให้เป็นแบบนี้ จะได้ไม่เผลอเปิดสิทธิ์ทิ้งไว้)
    SLIP_APPROVER_IDS = frozenset(
        part.strip()
        for part in os.getenv("SLIP_APPROVER_IDS", "").split(",")
        if part.strip()
    )


config = Config()