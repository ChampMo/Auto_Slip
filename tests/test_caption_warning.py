from datetime import datetime as _dt, timedelta as _td, timezone as _tz
"""ทดสอบ: ข้อความในแชทบวกเลขไม่ลง ต้องบังคับให้คนตัดสิน ห้ามบอทตัดสินเอง"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "warn.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111"

for module_name in [
    "telegram", "telegram.ext", "telegram.error",
    "gspread", "gspread.exceptions",
    "google", "google.oauth2", "google.oauth2.service_account",
    "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
    "requests", "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(module_name, MagicMock(name=module_name))

gspread_exceptions = types.ModuleType("gspread.exceptions")
gspread_exceptions.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
gspread_exceptions.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = gspread_exceptions
sys.modules["gspread"].exceptions = gspread_exceptions
sys.modules["telegram.error"].TimedOut = type("TimedOut", (Exception,), {})
sys.modules["telegram.error"].NetworkError = type("NetworkError", (Exception,), {})

import bot.handlers as h  # noqa: E402
from bot.verification_flow import VerificationDecision, determine_verification_action  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction  # noqa: E402

init_db()

failed = 0
sheet_writes = []


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


# แต่ละเคสต้องใช้ payload ของตัวเอง ไม่งั้นจะไปชนกับด่านเช็คสลิปซ้ำ
SLIP_AMOUNTS = {"QR_200A": 200.0, "QR_200B": 200.0, "QR_300A": 300.0}


def fake_verify_slip(payload):
    return {
        "success": True,
        "amount": SLIP_AMOUNTS[payload],
        "payload_amount": SLIP_AMOUNTS[payload],
        "sender": "นาย ดุลยฤทธิ์ ส",
        "receiver": "KKP-LS",
        "receiver_bank_code": "2984",
        "receiver_bank_matches": True,
        "transfer_at": _dt(2026, 8, 9, 0, 18, tzinfo=_tz(_td(hours=7))),
        "raw_data": {"payload": payload},
    }


h.verify_slip = fake_verify_slip
h.append_to_sheet = lambda entry: (sheet_writes.append(entry.batch_id), (True, ""))[1]


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, reply_to_message_id=None, reply_markup=None,
                           message_thread_id=None):
        self.messages.append({"text": text, "keyboard": reply_markup})
        return SimpleNamespace(message_id=9000 + len(self.messages))

    @property
    def last(self):
        return self.messages[-1] if self.messages else None


async def run_group(qr_list, caption, msg_id, photo_count=1):
    bot = FakeBot()
    await h.process_slip_group(bot, "-100111", msg_id, caption, qr_list, photo_count)
    return bot.last


def slip_by_msg(msg_id):
    with SessionLocal() as db:
        return db.query(Transaction).filter(Transaction.msg_id == str(msg_id)).first()


NAME_LINE = "FULL NAME : ดุลยฤทธิ์ ส\n"


async def main():
    print("=== ตรรกะการตัดสิน ===")
    check(
        "ทุกอย่างตรง แต่แชทบวกไม่ลง -> ให้คนตัดสิน",
        determine_verification_action(True, True, True, True, caption_unreliable=True),
        VerificationDecision.MANUAL_REVIEW,
    )
    check(
        "  ยอดไม่ตรง + แชทบวกไม่ลง -> ห้าม auto reject",
        determine_verification_action(True, False, True, True, caption_unreliable=True),
        VerificationDecision.MANUAL_REVIEW,
    )
    # ธงเรื่องข้อความกันได้แค่เรื่อง "ยอดเงิน" เท่านั้น
    # บัญชีผู้รับเป็นคนละเรื่อง — ข้อความจะพิมพ์ผิดยังไง เงินที่เข้าบัญชีคนอื่น
    # ก็ไม่กลายเป็นเข้าบัญชีเรา จึงต้องปฏิเสธอัตโนมัติตามปกติ
    check(
        "  ธนาคารไม่ตรง + แชทบวกไม่ลง -> ยังปฏิเสธอัตโนมัติ",
        determine_verification_action(True, True, True, False,
                                      caption_unreliable=True, bank_resolved=True),
        VerificationDecision.AUTO_REJECT,
    )
    check(
        "  แต่ถ้าชี้บัญชีไม่ได้ ต้องให้คนดู",
        determine_verification_action(True, True, True, False,
                                      caption_unreliable=True, bank_resolved=False),
        VerificationDecision.MANUAL_REVIEW,
    )
    check(
        "  แชทบวกลงตัว -> ตัดสินเองได้ตามปกติ",
        determine_verification_action(True, True, True, True, caption_unreliable=False),
        VerificationDecision.AUTO_RECEIVE,
    )

    print("\n=== ของจริง: '100+100 = 300' แต่สลิปมี 200 ===")
    caption = NAME_LINE + "Amount 100+100 = 300 HB"
    msg = await run_group(["QR_200A"], caption, msg_id=700)
    check("ขึ้นปุ่มถาม ไม่ auto reject", "Manual review required" in msg["text"], True)
    check("  มีปุ่มให้กด", msg["keyboard"] is not None, True)
    check("  เตือนว่าบวกไม่ลง", "does not add up" in msg["text"], True)
    check("  บอกผลบวกจริง", "100 + 100 = 200" in msg["text"], True)
    check("  บอกยอดที่เขาเขียน", "says 300" in msg["text"], True)
    check("  ไม่เขียนลงชีท", sheet_writes, [])
    check("  สถานะยังรอคนตัดสิน", slip_by_msg(700).status, "pending")
    check("  เก็บคำเตือนไว้ใน DB", "does not add up" in (slip_by_msg(700).caption_warning or ""), True)
    check("  ยึดยอดที่เขาเขียนไว้เทียบ", slip_by_msg(700).chat_amount, 300.0)

    print("\n=== '100+100 = 300' แล้วสลิปมี 300 จริง ก็ยังต้องถาม ===")
    msg = await run_group(["QR_300A"], caption, msg_id=701)
    check("ยังขึ้นปุ่มถาม ไม่ auto receive", "Manual review required" in msg["text"], True)
    check("  เตือนว่าบวกไม่ลง", "does not add up" in msg["text"], True)
    check("  ข้อยอดเงินอยู่ในกลุ่มที่ผ่าน", "Amount 300.00 THB" in msg["text"], True)
    check("  ยังไม่เขียนลงชีท", sheet_writes, [])

    print("\n=== '100+100 = 200' บวกลงตัว -> ทำงานอัตโนมัติตามปกติ ===")
    ok_caption = NAME_LINE + "Amount 100+100 = 200 HB"
    msg = await run_group(["QR_200B"], ok_caption, msg_id=702)
    check("รับอัตโนมัติ", "✅ Received automatically" in msg["text"], True)
    check("  เขียนลงชีทแล้ว", len(sheet_writes), 1)
    check("  ไม่มีคำเตือนค้างไว้", slip_by_msg(702).caption_warning, None)

    print("\n=== อ่าน QR ไม่ออก + แชทบวกไม่ลง ===")
    msg = await run_group([], caption, msg_id=703)
    check("ทวง QR", "🔍 QR code needed" in msg["text"], True)
    check("  เตือนว่าบวกไม่ลง", "does not add up" in msg["text"], True)
    check("  ยังโชว์ยอดที่เขาแจ้ง", "300.00 THB" in msg["text"], True)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
