import logging

import requests

from core.config import config

logger = logging.getLogger(__name__)

TELEGRAM_API_URL = "https://api.telegram.org"


def send_telegram_message(chat_id: str, text: str) -> bool:
    """ส่งข้อความเข้ากลุ่มโดยตรงผ่าน Bot API

    ใช้สำหรับงานที่รันนอก event loop ของบอท (เช่น scheduler ที่อยู่คนละเธรด)
    ซึ่งเรียก bot.send_message ที่เป็น async ไม่ได้
    """
    if not config.BOT_TOKEN:
        logger.error("Cannot send notification: BOT_TOKEN is not configured")
        return False

    # กลุ่มที่ใช้ Topics ถ้าไม่ระบุหัวข้อ ข้อความจะไปโผล่ที่ General ซึ่งอาจไม่มีใครเฝ้า
    from core.matcher import GROUP_TOPIC

    payload = {"chat_id": chat_id, "text": text}
    topic = GROUP_TOPIC.get(str(chat_id))
    if topic:
        payload["message_thread_id"] = int(topic)

    try:
        response = requests.post(
            f"{TELEGRAM_API_URL}/bot{config.BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
    except Exception as exc:
        logger.error("Failed to send notification | chat_id=%s | error=%s", chat_id, exc)
        return False

    if response.status_code != 200:
        logger.error(
            "Failed to send notification | chat_id=%s | status=%s | body=%s",
            chat_id, response.status_code, str(response.text)[:200],
        )
        return False

    logger.info("Notification sent | chat_id=%s", chat_id)
    return True
