# -*- coding: utf-8 -*-
"""ทดสอบทางกลับของสลิปที่อ่าน QR ไม่ครบ

สองอาการที่เจอจากการใช้จริง:
  1. รูปเดียวมี QR สองดวง อ่านออกดวงเดียว -> ยอดขาด -> ถูกปฏิเสธอัตโนมัติ
     แล้วไม่มีทางเติม QR ที่ขาดเลย แม้จะ /recheck
  2. กด "QR เสียจนอ่านไม่ได้" แล้วพิมพ์วันเวลาตอบกลับ กลับโดนตอบว่า
     "ไม่ใช่ QR" เพราะข้อความเดิมยังถูกจดไว้ว่าเป็นคำขอ QR อยู่ -> ค้างถาวร
"""
import asyncio
import io
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

# คอนโซลวินโดวส์เป็น cp874 ตัว emoji ที่ init_db พิมพ์จะทำให้ล้มก่อนเริ่มเทส
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "addqr.db")
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111"
for _leak in ("VIP_WE_TOPIC_ID", "VIP_12_TOPIC_ID", "API_ID", "API_HASH"):
    os.environ.pop(_leak, None)

for m in ["telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
          "google", "google.oauth2", "google.oauth2.service_account", "googleapiclient",
          "googleapiclient.discovery", "googleapiclient.errors", "requests",
          "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, MagicMock(name=m))
ge = types.ModuleType("gspread.exceptions")
ge.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
ge.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = ge
sys.modules["telegram.error"].TimedOut = type("TimedOut", (Exception,), {})
sys.modules["telegram.error"].NetworkError = type("NetworkError", (Exception,), {})

# ปุ่มต้องเป็นคลาสจริง ไม่งั้นอ่าน callback_data จาก MagicMock ไม่ได้
class _FakeButton:
    def __init__(self, text, callback_data=None, **kwargs):
        self.text = text
        self.callback_data = callback_data


class _FakeMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard


sys.modules["telegram"].InlineKeyboardButton = _FakeButton
sys.modules["telegram"].InlineKeyboardMarkup = _FakeMarkup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bot.handlers import handle_qr_text_reply, load_waiting_slip  # noqa: E402
from core.matcher import NEEDS_QR_STATUS  # noqa: E402
from database.models import Transaction  # noqa: E402
from database.session import SessionLocal, init_db  # noqa: E402

init_db()

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r} want {want!r}")


CHAT = "-100111"
QUESTION_MSG_ID = 555


def make_slip(batch_id, question_msg_id):
    with SessionLocal() as db:
        db.query(Transaction).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status=NEEDS_QR_STATUS,
            chat_id=CHAT, msg_id="900", raw_caption="ID 1 / 200",
            qr_request_msg_id=None if question_msg_id is None else str(question_msg_id),
        ))
        db.commit()


class FakeMessage:
    def __init__(self, chat_id, text, reply_to_id):
        self.chat_id = chat_id
        self.text = text
        self.message_id = 9001
        self.reply_to_message = types.SimpleNamespace(message_id=reply_to_id)
        self.from_user = types.SimpleNamespace(id=111, first_name="Approver", username="a")
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        reply = FakeMessage(self.chat_id, text, self.message_id)
        return reply


# ── อาการ 2: ยังรอ QR อยู่ -> ข้อความที่ตอบมาต้องถูกตัวรับ QR คว้าไป ──
make_slip("batchA", QUESTION_MSG_ID)
check("ยังรอ QR อยู่ = หาสลิปเจอ",
      load_waiting_slip(CHAT, QUESTION_MSG_ID) is not None, True)

msg = FakeMessage(CHAT, "12/08/69 08.46", QUESTION_MSG_ID)
check("ยังรอ QR อยู่ = ตัวรับ QR คว้าคำตอบไป",
      asyncio.run(handle_qr_text_reply(msg)), True)

# ── หลังกด "QR เสีย" ต้องเลิกรอ QR -> คำตอบวันเวลาต้องผ่านไปถึงตัวรับเวลา ──
make_slip("batchB", None)
check("เลิกรอ QR แล้ว = หาสลิปไม่เจอ",
      load_waiting_slip(CHAT, QUESTION_MSG_ID), None)

msg = FakeMessage(CHAT, "12/08/69 08.46", QUESTION_MSG_ID)
check("เลิกรอ QR แล้ว = ตัวรับ QR ปล่อยผ่าน",
      asyncio.run(handle_qr_text_reply(msg)), False)
check("เลิกรอ QR แล้ว = ไม่บ่นว่าไม่ใช่ QR", msg.replies, [])

# ── ปุ่มเติม QR ──
from bot.keyboards import get_add_qr_keyboard, get_approval_keyboard  # noqa: E402


def callbacks(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


check("ปุ่มเดี่ยวเติม QR มีจริง",
      any(c.startswith("addqr_") for c in callbacks(get_add_qr_keyboard("batch123"))), True)
check("ตอนบอทตรวจรอบแรก ไม่มีปุ่มเติม QR",
      any(c.startswith("addqr_") for c in callbacks(get_approval_keyboard("batch123"))), False)
check("ตอน /recheck มีปุ่มเติม QR",
      any(c.startswith("addqr_")
          for c in callbacks(get_approval_keyboard("batch123", with_add_qr=True))), True)

# ── กันของเดิมกลับมา ──
handlers_src = io.open(os.path.join(ROOT, "bot", "handlers.py"), encoding="utf-8").read()
commands_src = io.open(os.path.join(ROOT, "bot", "commands.py"), encoding="utf-8").read()

noqr_branch = handlers_src.split('callback_data.startswith("noqr_")')[-1]
noqr_branch = noqr_branch.split('callback_data.startswith("bank_")')[0]
check("ปุ่ม QR เสีย ล้างสถานะรอ QR",
      "txn.qr_request_msg_id = None" in noqr_branch, True)

check("ใบที่ถูกปฏิเสธเพราะยอดขาด มีปุ่มเติม QR ติดไปด้วย",
      "get_add_qr_keyboard(batch_id) if missing_slip_likely" in handlers_src, True)
check("/recheck เรียกปุ่มเติม QR กลับมา",
      "get_approval_keyboard(batch_id, with_add_qr=True)" in commands_src, True)
addqr_branch = handlers_src.split('callback_data.startswith("addqr_")')[-1]
addqr_branch = addqr_branch.split('callback_data.startswith("dup_")')[0]
check("ปุ่มเติม QR แก้ข้อความเดิม ไม่โพสต์ใหม่",
      "query.message.reply_text(" in addqr_branch, False)
check("ปุ่มเติม QR ยังจดข้อความที่ต้องตอบกลับไว้",
      "qr_request_msg_id = str(question.message_id)" in addqr_branch, True)

if failures:
    print("FAILED")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("test_add_qr_recovery: OK")
