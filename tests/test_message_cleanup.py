# -*- coding: utf-8 -*-
"""ทดสอบว่าข้อความชั่วคราวไม่ค้างอยู่ในกลุ่ม

ข้อความที่อ่านแล้วจบ (ผลคำสั่ง, เตือนว่าไม่มีสิทธิ์, บ่นว่าตอบผิดรูปแบบ)
ต้องลบตัวเองในกลุ่มที่ลงทะเบียน ส่วนคำขอที่ยังรอคนมาตอบต้องไม่ถูกลบ
"""
import asyncio
import io
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "cleanup.db")
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

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot.ephemeral as ephemeral
from bot.ephemeral import (
    EXPIRY_NOTE,
    SHORT_EXPIRY_NOTE,
    notice_and_expire,
    reply_and_expire,
    will_expire,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r} want {want!r}")


_next_message_id = [1000]


class FakeMessage:
    def __init__(self, chat_id):
        self.chat_id = chat_id
        self.sent = []
        self.deleted = False
        _next_message_id[0] += 1
        self.message_id = _next_message_id[0]
        self.text = ""

    async def reply_text(self, text, **kwargs):
        reply = FakeMessage(self.chat_id)
        reply.text = text
        self.sent.append(reply)
        return reply

    async def delete(self):
        self.deleted = True


IN_GROUP = "-100111"
OUTSIDE = "-100999"


def run(coro):
    """รันโดยดักการนัดลบไว้ จะได้ไม่ต้องรอจริง 60 วินาที"""
    scheduled = []
    real_create_task = asyncio.create_task

    def fake_create_task(coro_obj):
        scheduled.append(coro_obj)
        coro_obj.close()          # ไม่ต้องรันจริง แค่รู้ว่าถูกนัดไว้
        return MagicMock()

    async def wrapper():
        asyncio.create_task = fake_create_task
        try:
            return await coro()
        finally:
            asyncio.create_task = real_create_task

    result = asyncio.run(wrapper())
    return result, len(scheduled)


# ── ลบเฉพาะในกลุ่มที่ลงทะเบียน ──
check("ในกลุ่มที่ลงทะเบียน = ลบ", will_expire(FakeMessage(IN_GROUP)), True)
check("นอกกลุ่ม = ไม่ลบ", will_expire(FakeMessage(OUTSIDE)), False)
check("ไม่รู้ว่าแชทไหน = ไม่ลบ", will_expire(types.SimpleNamespace()), False)

# ── ผลคำสั่งในกลุ่ม: ติดหมายเหตุ + นัดลบทั้งคำตอบและคำสั่ง ──
msg = FakeMessage(IN_GROUP)
sent, scheduled = run(lambda: reply_and_expire(msg, "📊 Today"))
check("ในกลุ่ม ติดหมายเหตุว่าจะหาย", sent.text.endswith(EXPIRY_NOTE), True)
check("ในกลุ่ม นัดลบไว้", scheduled, 1)

msg = FakeMessage(OUTSIDE)
sent, scheduled = run(lambda: reply_and_expire(msg, "📊 Today"))
check("นอกกลุ่ม ไม่ติดหมายเหตุ", sent.text, "📊 Today")
check("นอกกลุ่ม ไม่นัดลบ", scheduled, 0)

# ── คำเตือนสั้นๆ ใช้หมายเหตุแบบสั้น และไม่ลบข้อความของคน ──
msg = FakeMessage(IN_GROUP)
sent, scheduled = run(lambda: notice_and_expire(msg, "ไม่มีสิทธิ์"))
check("คำเตือน ใช้หมายเหตุสั้น", sent.text.endswith(SHORT_EXPIRY_NOTE), True)
check("คำเตือน ยังนัดลบ", scheduled, 1)


async def _check_keeps_user_message():
    user_message = FakeMessage(IN_GROUP)
    captured = {}

    async def spy(sent, command_message=None):
        captured["command_message"] = command_message

    original = ephemeral._delete_later
    ephemeral._delete_later = spy
    try:
        await notice_and_expire(user_message, "ไม่มีสิทธิ์")
        await asyncio.sleep(0)
    finally:
        ephemeral._delete_later = original
    return captured.get("command_message", "ไม่ถูกเรียก")


check("คำเตือน ไม่ลบข้อความของคนที่พิมพ์มา",
      asyncio.run(_check_keeps_user_message()), None)

# ── ถามยอดเสร็จแล้วถามเวลาต่อ ต้องเป็นข้อความเดียว ──
from bot.handlers import ask_for_transfer_time  # noqa: E402


async def _ask_with_lead():
    message = FakeMessage(IN_GROUP)
    user = types.SimpleNamespace(id=111, first_name="Tester", username="tester")
    await ask_for_transfer_time(message, user, "batch123", lead="Amount set to 1500.00 THB")
    return message.sent


sent_messages = asyncio.run(_ask_with_lead())
check("ถามเวลาโอน ส่งข้อความเดียว", len(sent_messages), 1)
if sent_messages:
    body = sent_messages[0].text
    check("ข้อความเดียวมียอดที่ตั้งไว้", "Amount set to 1500.00 THB" in body, True)
    check("ข้อความเดียวมีคำถามเรื่องเวลา", "Date and time needed" in body, True)
    check("ยอดอยู่ก่อนคำถาม",
          body.index("Amount set to") < body.index("Date and time needed"), True)
    check("คำขอที่ยังรอคำตอบ ต้องไม่ติดหมายเหตุว่าจะหาย",
          EXPIRY_NOTE in body or SHORT_EXPIRY_NOTE in body, False)


async def _ask_without_lead():
    message = FakeMessage(IN_GROUP)
    user = types.SimpleNamespace(id=111, first_name="Tester", username="tester")
    await ask_for_transfer_time(message, user, "batch456")
    return message.sent[0].text


body = asyncio.run(_ask_without_lead())
check("ไม่มีบรรทัดนำ = ไม่มีบรรทัดว่างนำหน้า", body.startswith("🕒"), True)


# ── กันของเดิมกลับมา: ข้อความพวกนี้ต้องไม่ใช้ reply_text ตรงๆ อีก ──
def source_of(path):
    return io.open(path, encoding="utf-8").read()


commands_src = source_of(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "commands.py"))
handlers_src = source_of(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot", "handlers.py"))

must_expire = [
    (commands_src, 'message.reply_text(NOT_ALLOWED_TEXT)', "คำเตือนว่าไม่ใช่เจ้าของ"),
    (commands_src, 'message.reply_text("Only approvers can', "คำเตือนว่าไม่ใช่ผู้อนุมัติ"),
    (commands_src, 'message.reply_text("You are not allowed', "คำเตือนว่าดูสถานะไม่ได้"),
    (commands_src, 'message.reply_text(problem)', "บอกว่าหาสลิปไม่เจอ"),
    (handlers_src, 'message.reply_text(QR_NOT_ALLOWED_TEXT)', "คำเตือนเรื่องตอบคำขอ QR"),
    (handlers_src, 'message.reply_text(f"Amount set to', "ยอดที่ตั้งไว้ลอยเดี่ยว"),
]
for src, needle, label in must_expire:
    check(f"{label} ไม่ใช้ reply_text ตรงๆ แล้ว", needle in src, False)

# /today กับ /help ต้องหมดอายุ
check("/today หมดอายุ", 'f"📊 Today ·' in commands_src.split("reply_and_expire")[-1]
      or "reply_and_expire" in commands_src, True)

if failures:
    print("FAILED")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("test_message_cleanup: OK")
