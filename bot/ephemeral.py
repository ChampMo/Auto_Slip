"""ข้อความชั่วคราวที่ลบตัวเองในกลุ่ม

แยกออกมาเป็นไฟล์ของตัวเองเพราะทั้ง commands.py และ handlers.py ต้องใช้
ถ้าปล่อยไว้ที่ commands.py แล้วให้ handlers.py ไป import จะเกิด import วนกัน
(commands.py import จาก handlers.py อยู่แล้ว)

ใช้กับข้อความที่อ่านแล้วจบ — ผลของคำสั่งอ่านค่า, คำเตือนว่าไม่มีสิทธิ์,
คำบ่นว่าตอบผิดรูปแบบ  ห้ามใช้กับข้อความที่ยังต้องมีคนมาตอบกลับ
(คำขอ QR / ยอด / เวลา) เพราะลบไปแล้วงานจะค้างโดยไม่มีใครรู้
"""
import asyncio
import logging

from core.matcher import GROUP_CATEGORY

logger = logging.getLogger(__name__)

# ข้อความตอบคำสั่งอ่านค่าจะหมดอายุเองในกลุ่ม เพื่อไม่ให้ผลลัพธ์เก่าค้างอยู่
# แล้วมีคนเลื่อนมาเจอทีหลังนึกว่าเป็นสถานะปัจจุบัน (เคยทำให้เข้าใจผิดมาแล้ว)
# นอกกลุ่มไม่ลบ เพราะคุยส่วนตัวไม่ได้รกใคร และมักอยากเก็บไว้อ่านย้อน
AUTO_DELETE_SECONDS = 60

# บอกไว้ในข้อความเลยว่าจะหายไปเอง ไม่งั้นคนกำลังอ่านอยู่แล้วมันหายจะงงว่าเกิดอะไรขึ้น
EXPIRY_NOTE = ("\n\n⏳ This message disappears in 1 minute — "
               "run the command again if you need it.")

# ข้อความสั้นๆ ที่อ่านจบในบรรทัดเดียว ไม่ต้องมีหมายเหตุยาวกว่าตัวข้อความเอง
SHORT_EXPIRY_NOTE = "\n\n⏳ หายเองใน 1 นาที"


async def _delete_later(sent, command_message=None):
    await asyncio.sleep(AUTO_DELETE_SECONDS)
    for target in (sent, command_message):
        if target is None:
            continue
        try:
            await target.delete()
        except Exception:
            # ลบข้อความของคนอื่นต้องเป็นแอดมิน ถ้าไม่ได้ก็ไม่เป็นไร ปล่อยไว้
            pass


def will_expire(message) -> bool:
    """ข้อความจะถูกลบทิ้งไหม — ลบเฉพาะในกลุ่มที่ลงทะเบียน"""
    return str(getattr(message, "chat_id", "")) in GROUP_CATEGORY


async def reply_and_expire(message, text, note=EXPIRY_NOTE, delete_command=True, **kwargs):
    """ตอบข้อความ แล้วนัดลบทิ้งถ้าอยู่ในกลุ่มที่ลงทะเบียน

    delete_command=False สำหรับกรณีที่ไม่ได้ตอบคำสั่ง แต่ตอบข้อความปกติของคน
    (เช่นเตือนว่าไม่มีสิทธิ์ตอบคำขอ QR) — ข้อความของเขาไม่ควรถูกลบไปด้วย
    """
    expiring = will_expire(message)
    sent = await message.reply_text(text + (note if expiring else ""), **kwargs)
    # ไม่รู้ว่าอยู่แชทไหน = ไม่ต้องนัดลบ ดีกว่าทำให้ทั้งคำสั่งล้มเพราะเรื่องความสะอาด
    if expiring:
        asyncio.create_task(_delete_later(sent, message if delete_command else None))
    return sent


async def notice_and_expire(message, text, **kwargs):
    """คำเตือนสั้นๆ ที่ไม่ได้มาจากคำสั่ง — ใช้หมายเหตุแบบสั้นและไม่ลบข้อความของคน"""
    return await reply_and_expire(
        message, text, note=SHORT_EXPIRY_NOTE, delete_command=False, **kwargs)
