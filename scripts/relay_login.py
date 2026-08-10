"""ล็อกอินตัวฟังครั้งแรก — python scripts/relay_login.py

ต้องรันในเทอร์มินัลที่พิมพ์ตอบได้ เพราะ Telegram จะส่ง OTP มาให้กรอก
ทำครั้งเดียวพอ จากนั้นได้ไฟล์ session มาแล้วบอทจะใช้ต่อได้เอง

ไฟล์ session = สิทธิ์เข้าบัญชีนั้นเต็มรูปแบบ หลุดเมื่อไหร่บัญชีถูกยึด
อยู่ใน .gitignore แล้ว อย่าเอาไปแปะที่ไหนเด็ดขาด
"""
import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.config import config  # noqa: E402

# จงใจไม่ import core.matcher เพราะมันลากฐานข้อมูลมาด้วย
# สคริปต์นี้ต้องรันได้จากเครื่องที่ไม่มี driver ของ Postgres ติดตั้ง
GROUP_IDS = [str(config.VIP_WE_CHAT_ID), str(config.VIP_12_CHAT_ID)]


async def main():
    if not (config.RELAY_API_ID and config.RELAY_API_HASH):
        print("❌ ยังไม่มี API_ID / API_HASH ใน .env")
        return 1

    from telethon import TelegramClient

    session_path = pathlib.Path(config.RELAY_SESSION)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(
        str(session_path), int(config.RELAY_API_ID), config.RELAY_API_HASH,
        device_model="Auto Slip Relay",
    )
    print(f"กำลังล็อกอิน... (session: {session_path}.session)")
    await client.start()

    me = await client.get_me()
    print(f"\n✅ ล็อกอินสำเร็จ: {me.first_name} @{me.username or '-'} (id {me.id})")

    print("\nกลุ่มที่ระบบจะฟัง:")
    for chat_id in GROUP_IDS:
        try:
            entity = await client.get_entity(int(chat_id))
            print(f"  ✅ {chat_id}  {getattr(entity, 'title', '?')}")
        except Exception as exc:
            print(f"  ❌ {chat_id}  เข้าไม่ถึง — บัญชีนี้อยู่ในกลุ่มหรือยัง? ({exc})")

    await client.disconnect()
    print("\nเรียบร้อย รีสตาร์ทบอทได้เลย ตัวฟังจะเปิดเองตอนบูต")
    return 0


sys.exit(asyncio.run(main()))
