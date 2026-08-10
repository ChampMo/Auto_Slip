"""ตัวฟังข้อความจากบอทตัวอื่น (MTProto)

Telegram ไม่ส่งข้อความที่บอทตัวหนึ่งโพสต์ ให้บอทอีกตัวรับรู้ — เป็นข้อจำกัดของ
แพลตฟอร์มที่แก้ในโค้ดบอทไม่ได้ สลิปที่ SysAlertBot โพสต์จึงไม่มีทางถึง Auto Slip เลย

ไฟล์นี้ล็อกอินด้วย "บัญชีคนจริง" ซึ่งเห็นข้อความบอทได้ แล้วส่งต่อเข้ากระบวนการเดิม
ภายในโปรแกรมโดยตรง ไม่ต้องฟอร์เวิร์ดเข้ากลุ่มให้รก และไม่ต้องมีกลุ่มที่สอง

Auto Slip ยังเป็นคนตอบเหมือนเดิมทุกอย่าง (ปุ่ม Receive/Reject ใช้ได้ครบ) เพราะการ
ตอบกลับต้องการแค่ "เลขข้อความ" ไม่จำเป็นต้องเคยได้รับ update ของข้อความนั้นมาก่อน
"""
import logging
import os
from datetime import datetime

from core.config import config
from core.matcher import GROUP_CATEGORY, topic_allowed
from core.scanner import file_sha256, read_qr_code

logger = logging.getLogger(__name__)

# เวลารอรวมอัลบั้ม ใช้ค่าเดียวกับฝั่งบอทจะได้พฤติกรรมเหมือนกัน
_client = None
_state = {"connected": False, "last_seen": None, "last_seen_at": None,
          "last_error": ""}


def relay_enabled() -> bool:
    return bool(config.RELAY_API_ID and config.RELAY_API_HASH)


def relay_status() -> dict:
    """สถานะไว้ให้ /health รายงาน — ตัวฟังหลุดแล้วไม่มีใครรู้คือสิ่งที่อันตรายที่สุด

    ถาม client ตรงๆ ทุกครั้ง ไม่เชื่อค่าที่จำไว้ตอนเชื่อมต่อสำเร็จ
    เพราะ session ถูก revoke ระหว่างทางได้ แล้วค่าที่จำไว้จะโกหกว่ายังต่ออยู่
    """
    connected = _state["connected"]
    if _client is not None:
        try:
            connected = bool(_client.is_connected())
        except Exception:
            connected = False
    return dict(_state, connected=connected, enabled=relay_enabled())


def _own_bot_id() -> str:
    """เลข id ของ Auto Slip เอง — token ขึ้นต้นด้วยเลขนี้เสมอ

    ต้องรู้เพื่อไม่ให้ตัวฟังเอาข้อความที่ Auto Slip โพสต์เอง กลับเข้าไปตรวจซ้ำ
    ซึ่งจะกลายเป็นวนไม่รู้จบ
    """
    return str(config.BOT_TOKEN or "").split(":")[0]


def _topic_of(message) -> int | None:
    """หมายเลขหัวข้อของข้อความ (กลุ่มที่เปิด Topics)

    Telethon เก็บไว้คนละที่กับ Bot API จึงต้องแกะเอง
    ข้อความแรกของหัวข้อจะมีแค่ reply_to_msg_id ส่วนข้อความถัดมามี reply_to_top_id
    """
    reply_to = getattr(message, "reply_to", None)
    if reply_to is None:
        return None
    if not getattr(reply_to, "forum_topic", False):
        return None
    return getattr(reply_to, "reply_to_top_id", None) or getattr(reply_to, "reply_to_msg_id", None)


def should_handle(chat_id, sender_id, is_bot: bool, allowed_bots: set,
                  username: str = "") -> bool:
    """ข้อความนี้เป็นสลิปที่ตัวฟังต้องรับผิดชอบไหม

    รับเฉพาะข้อความของ "บอทตัวอื่น" เท่านั้น — ข้อความของคนบอทหลักเห็นเองอยู่แล้ว
    ถ้าตัวฟังรับด้วยจะกลายเป็นตรวจซ้ำสองรอบต่อสลิปหนึ่งใบ

    RELAY_SOURCE_BOTS ตั้งเป็น username ก็ได้ เลข id ก็ได้ คนตั้งค่าจะได้ไม่ต้องไปหา id
    การกันข้อความของตัวเองอยู่ก่อนการเทียบรายชื่อเสมอ เผื่อมีคนเผลอใส่ชื่อบอทตัวเองลงไป
    """
    if str(chat_id) not in GROUP_CATEGORY:
        return False
    if str(sender_id) == _own_bot_id():
        return False

    if allowed_bots:
        # ระบุชื่อไว้ชัดเจนแล้ว เชื่อรายชื่อนั้นเลย ไม่ต้องรอให้ Telethon ยืนยันว่าเป็นบอท
        # (get_sender คืน None ได้ถ้าผู้ส่งยังไม่อยู่ใน cache แล้ว is_bot จะกลายเป็น False
        #  ทั้งที่จริงเป็นบอท — เคยทำให้ตัวฟังเงียบสนิทโดยไม่มีอะไรบอก)
        names = {str(sender_id).lower(), (username or "").lower().lstrip("@")}
        return bool(names & allowed_bots)

    # ไม่ได้ระบุชื่อ = รับจากบอทตัวไหนก็ได้ ตรงนี้ต้องรู้ให้ชัดว่าเป็นบอทจริง
    return is_bot


async def _handle_message(bot, message):
    """โหลดรูป อ่าน QR แล้วส่งเข้ากระบวนการเดียวกับสลิปที่คนส่งเอง"""
    from bot.handlers import collect_media_group_photo, process_slip_group

    chat_id = str(message.chat_id)
    msg_id = message.id
    caption = message.message or ""

    thread_id = _topic_of(message)
    if not topic_allowed(chat_id, thread_id):
        logger.info("Relay ignored a slip from another topic | msg_id=%s", msg_id)
        return

    temp_path = f"relay_{chat_id}_{msg_id}.img".replace("-", "")
    saved = None
    try:
        saved = await message.download_media(file=temp_path)
        if not saved:
            logger.info("Relay message has no image | msg_id=%s", msg_id)
            return
        qr_list = read_qr_code(saved)
        photo_hash = file_sha256(saved)
    except Exception as exc:
        logger.exception("Relay could not read the picture | msg_id=%s | error=%s", msg_id, exc)
        return
    finally:
        # Telethon อาจคืนชื่อไฟล์ที่ไม่ตรงกับที่ขอไป ต้องลบตามชื่อจริงด้วย
        for path in {temp_path, saved}:
            if not path:
                continue
            try:
                os.remove(path)
            except OSError:
                pass

    _state["last_seen"] = msg_id
    _state["last_seen_at"] = datetime.utcnow()
    logger.info("Relay picked up a slip | msg_id=%s | qr_count=%s", msg_id, len(qr_list))

    # อัลบั้มมาเป็นคนละข้อความเหมือนฝั่ง Bot API จึงใช้ตัวรวมอัลบั้มตัวเดิมได้เลย
    grouped = getattr(message, "grouped_id", None)
    if grouped:
        await collect_media_group_photo(
            f"relay:{grouped}", bot, chat_id, msg_id, caption, qr_list, photo_hash,
            message_thread_id=thread_id, include_source_link=True,
        )
        return

    # ต้องบอกหมายเลขหัวข้อด้วย ไม่งั้น Telegram จะพยายามตอบใน General
    # แล้วหาข้อความต้นทางไม่เจอ (BadRequest: Message to be replied not found)
    await process_slip_group(bot, chat_id, msg_id, caption, qr_list,
                             photo_count=1, photo_hashes=[photo_hash],
                             message_thread_id=thread_id,
                             include_source_link=True)


async def start_relay(bot):
    """เปิดตัวฟัง คืน client ถ้าเปิดได้ คืน None ถ้าไม่ได้ตั้งค่าไว้

    ตั้งใจไม่ให้ล้มทั้งโปรแกรมถ้าตัวฟังเปิดไม่ได้ — บอทหลักยังต้องทำงานกับสลิป
    ที่คนส่งเองได้ตามปกติ แค่เสียความสามารถในการเห็นข้อความของบอทตัวอื่น
    """
    global _client

    if not relay_enabled():
        logger.info("Relay is not configured (RELAY_API_ID / RELAY_API_HASH missing)")
        return None

    from telethon import TelegramClient, events

    allowed_bots = {b.strip().lower() for b in config.RELAY_SOURCE_BOTS if b.strip()}
    _client = TelegramClient(
        config.RELAY_SESSION, int(config.RELAY_API_ID), config.RELAY_API_HASH,
        device_model="Auto Slip Relay",   # ตั้งชื่อให้จำได้ จะได้ไม่มีใครเผลอเตะทิ้ง
    )

    # จงใจไม่ใส่ chats= ให้ Telethon กรอง เพราะถ้ามีกลุ่มไหน resolve ไม่ได้
    # (บัญชียังไม่ได้เข้ากลุ่มนั้น) ตัวฟังจะล้มทั้งตัว รวมกลุ่มที่เข้าถึงได้ด้วย
    # กรองเองในนี้ปลอดภัยกว่า เสียแค่ต้องรับ event ที่ไม่เกี่ยวมาทิ้งบ้าง
    @_client.on(events.NewMessage())
    async def _on_new_message(event):
        message = event.message
        if str(message.chat_id) not in GROUP_CATEGORY:
            return

        sender_id = getattr(message, "sender_id", None)
        try:
            sender = await event.get_sender()
        except Exception as exc:
            logger.info("Relay could not read the sender | msg_id=%s | error=%s", message.id, exc)
            sender = None
        is_bot = bool(getattr(sender, "bot", False))
        username = getattr(sender, "username", "") or ""

        # เห็นทุกข้อความในกลุ่มที่ลงทะเบียน จะได้ไล่ได้ว่าทำไมใบไหนไม่เข้า
        # ตัวฟังที่เงียบโดยไม่บอกอะไรเลยคือสิ่งที่ไล่ปัญหายากที่สุด
        logger.info(
            "Relay saw a message | msg_id=%s | sender=%s | is_bot=%s | username=%s "
            "| topic=%s | has_media=%s",
            message.id, sender_id, is_bot, username or "-",
            _topic_of(message), bool(message.media),
        )

        if not should_handle(message.chat_id, sender_id, is_bot, allowed_bots, username):
            logger.info("Relay skipped it (not a slip from another bot) | msg_id=%s", message.id)
            return
        try:
            await _handle_message(bot, message)
        except Exception as exc:
            # ห้ามให้สลิปใบเดียวทำให้ตัวฟังหยุดรับใบถัดไป
            logger.exception("Relay failed on a message | msg_id=%s | error=%s",
                             message.id, exc)

    await _client.start()
    _state["connected"] = True
    me = await _client.get_me()
    logger.info("Relay connected as %s (id %s)", getattr(me, "username", "?"), getattr(me, "id", "?"))
    print(f"📡 ตัวฟังข้อความจากบอทอื่นทำงานแล้ว (บัญชี {getattr(me, 'username', me.id)})")
    return _client


async def stop_relay():
    if _client is not None and _client.is_connected():
        await _client.disconnect()
        _state["connected"] = False
