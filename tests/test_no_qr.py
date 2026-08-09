from types import SimpleNamespace
"""ทดสอบเคสอ่าน QR ไม่ออก: ต้องทวง QR ก่อน ไม่ปล่อยให้รับเข้าชีทโดยไม่ตรวจกับธนาคาร"""
import asyncio
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "noqr.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"

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
from core.names import get_first_names  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction, UsedQR  # noqa: E402

init_db()
h.append_to_sheet = lambda txn: (True, "")

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, reply_to_message_id=None, reply_markup=None):
        self.messages.append({"text": text, "reply_to": reply_to_message_id, "keyboard": reply_markup})
        return SimpleNamespace(message_id=9000 + len(self.messages))


def sheet_trans_id(txn):
    """ลอจิกเดียวกับ services/gsheets.py append_to_sheet"""
    return txn.chat_trans_id or get_first_names(txn.sender_names) or get_first_names(txn.chat_fullname) or ""


CAP_TRANS = "TRANS ID : 0000123\nFULL NAME : ดุลยฤทธิ์ สมบูรณ์\nAMOUNT THB : 400.00"
CAP_USER = "Hi team, User :  benz4455\nAmount : THB 250\nKKP - LEASON"


async def run(qr_list, caption, photo_count, msg_id):
    bot = FakeBot()
    await h.process_slip_group(bot, "-100111", msg_id, caption, qr_list, photo_count)
    return bot.messages[-1] if bot.messages else None


async def main():
    print("=== รูปเดียวอ่าน QR ไม่ออก (format TRANS ID) ===")
    msg = await run([], CAP_TRANS, photo_count=1, msg_id=200)
    check("ทวง QR", "🔍 QR code needed" in msg["text"], True)
    check("  มีปุ่มให้กด", msg["keyboard"] is not None, True)
    check("  บอกว่าอ่าน QR ไม่ได้", "No QR code could be read from this photo" in msg["text"], True)
    check("  โชว์ข้อมูลจากแชท", "0000123" in msg["text"] and "400.00 THB" in msg["text"], True)
    check("  บอกวิธีตอบกลับ", "Reply to THIS message" in msg["text"], True)
    check("  ไม่มี backtick โผล่ในข้อความ", "`" in msg["text"], False)
    check("  ตอบกลับที่ข้อความเดิม", msg["reply_to"], 200)

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.msg_id == "200").first()
        check("  บันทึกลง DB แล้ว", txn is not None, True)
        check("  status รอ QR", txn.status, "needs_qr")
        check("  ไม่มี QR ผูกไว้", db.query(UsedQR).count(), 0)
        check("  เก็บยอดจากแชท", txn.chat_amount, 400.0)
        check("  ยังไม่รู้ยอดจาก API", txn.api_total_amount, None)
        check("  กด Receive ต้องถามธนาคาร", h.resolve_known_bank_value(txn), "")
        check("  Trans ID ที่จะลงชีท", sheet_trans_id(txn), "0000123")

    print("\n=== อัลบั้ม 2 รูปอ่านไม่ออกทั้งคู่ (format User ไม่มี TRANS ID) ===")
    msg = await run([], CAP_USER, photo_count=2, msg_id=201)
    check("ทวง QR", "🔍 QR code needed" in msg["text"], True)
    check("  นับจำนวนรูปถูก", "these 2 photos" in msg["text"], True)
    check("  โชว์ User จากแชท", "benz4455" in msg["text"], True)

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.msg_id == "201").first()
        check("  เก็บ user จากแชท", txn.chat_user_id, "benz4455")
        check("  เก็บยอดจากแชท", txn.chat_amount, 250.0)
        check("  Trans ID ที่จะลงชีท (ไม่มีชื่อเลย)", sheet_trans_id(txn), "")

    print("\n=== อ่านไม่ออก แต่แชทแจ้งชื่อมา -> ใช้ชื่อจากแชทลงชีท ===")
    await run([], "FULL NAME : ณัฏฐพา ส\nAMOUNT THB : 999", photo_count=1, msg_id=202)
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.msg_id == "202").first()
        check("ใช้ชื่อจากแชท", sheet_trans_id(txn), "ณัฏฐพา")

    print("\n=== แต่ละข้อความต้องแยกรายการกัน ไม่ทับกัน ===")
    await run([], CAP_TRANS, photo_count=1, msg_id=203)
    with SessionLocal() as db:
        check("จำนวนรายการทั้งหมด", db.query(Transaction).count(), 4)
        ids = {t.batch_id for t in db.query(Transaction).all()}
        check("  batch_id ไม่ซ้ำกัน", len(ids), 4)

    print("\n=== ส่งจากกลุ่มที่ไม่ได้ลงทะเบียน ต้องเงียบเหมือนเดิม ===")
    bot = FakeBot()
    await h.process_slip_group(bot, "-999999", 204, CAP_TRANS, [], 1)
    check("ไม่ตอบกลับ", len(bot.messages), 0)
    with SessionLocal() as db:
        check("  ไม่บันทึกลง DB", db.query(Transaction).filter(Transaction.msg_id == "204").count(), 0)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
