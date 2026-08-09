"""ทดสอบ: บอททำงานเฉพาะรูปที่มีข้อความกำกับ + ไม่มี QR และไม่มียอด = ปฏิเสธ"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "gate.db")
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
from core.matcher import REJECTED_STATUSES  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction, AuditLog  # noqa: E402

init_db()
h.append_to_sheet = lambda entry: (True, "")

failed = 0
downloads = []


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
        self.messages.append({"text": text, "keyboard": reply_markup})
        return SimpleNamespace(message_id=9000 + len(self.messages))

    @property
    def last(self):
        return self.messages[-1] if self.messages else None


def fake_download(message):
    downloads.append(message.message_id)
    return asyncio.sleep(0, result=(list(getattr(message, "qr", [])),
                                    f"hash_{message.message_id}"))


h.download_and_scan_photo = fake_download

CAP_WITH_AMOUNT = "Hi team, User :  benz4455\nAmount : THB 400"
CAP_NO_AMOUNT = "Hi team, please check this deposit\n-maryann"


def photo_message(msg_id, caption, qr=(), media_group_id=None):
    return SimpleNamespace(
        chat_id="-100111", message_id=msg_id, caption=caption,
        media_group_id=media_group_id, qr=list(qr), photo=[MagicMock()], document=None,
    )


async def send_photo(msg_id, caption, qr=(), media_group_id=None):
    bot = FakeBot()
    await h.handle_photo(
        SimpleNamespace(message=photo_message(msg_id, caption, qr, media_group_id)),
        SimpleNamespace(bot=bot),
    )
    return bot


def txn_by_msg(msg_id):
    with SessionLocal() as db:
        return db.query(Transaction).filter(Transaction.msg_id == str(msg_id)).first()


async def main():
    print("=== รูปเดี่ยวไม่มีข้อความกำกับ -> เมินเลย ===")
    downloads.clear()
    bot = await send_photo(10, "", qr=["QR_X"])
    check("ไม่ตอบอะไรในกลุ่ม", len(bot.messages), 0)
    check("  ไม่แม้แต่โหลดไฟล์", downloads, [])
    check("  ไม่สร้างรายการใน DB", txn_by_msg(10), None)

    bot = await send_photo(11, "   \n  ", qr=["QR_Y"])
    check("caption มีแต่ช่องว่าง ก็ถือว่าไม่มี", len(bot.messages), 0)
    check("  ยังไม่โหลดไฟล์", downloads, [])

    print("\n=== รูปเดี่ยวมีข้อความ -> ทำงานปกติ ===")
    downloads.clear()
    bot = await send_photo(12, CAP_NO_AMOUNT, qr=[])
    check("โหลดไฟล์แล้ว", downloads, [12])
    check("  มีการตอบกลับ", len(bot.messages), 1)

    print("\n=== ไม่มี QR + ไม่มียอด -> ปฏิเสธอัตโนมัติ ===")
    check("ทวง QR", "🔍 QR code needed" in bot.last["text"], True)
    check("  บอกเหตุผลว่าอ่าน QR ไม่ได้", "No QR code could be read" in bot.last["text"], True)
    check("  บอกวิธีส่ง QR แบบรูป", "picture of just the QR code" in bot.last["text"], True)
    check("  บอกเว็บที่ใช้อ่าน QR", "qrcodescan.in" in bot.last["text"], True)
    check("  มีปุ่มเผื่อ QR เสียจริง", bot.last["keyboard"] is not None, True)

    txn = txn_by_msg(12)
    check("  สถานะรอ QR", txn.status, "needs_qr")
    check("  ยังไม่จองแฮชรูป (ส่งรูปเดิมมาใหม่ได้)", txn.photo_hash is not None, True)
    with SessionLocal() as db:
        actions = [a.action for a in db.query(AuditLog).filter(AuditLog.qr_ref == txn.batch_id)]
    check("  audit บันทึกว่าไปทวง QR", "qr_requested" in actions, True)

    print("\n=== ไม่มี QR แต่มียอด -> ยังให้แอดมินตัดสินเหมือนเดิม ===")
    bot = await send_photo(13, CAP_WITH_AMOUNT, qr=[])
    check("ทวง QR", "🔍 QR code needed" in bot.last["text"], True)
    check("  มีปุ่มให้กด", bot.last["keyboard"] is not None, True)
    check("  โชว์ยอดที่แจ้งมา", "400.00 THB" in bot.last["text"], True)
    check("  สถานะรอ QR", txn_by_msg(13).status, "needs_qr")

    print("\n=== อัลบั้ม: caption อยู่ใบเดียว ใบอื่นต้องไม่ถูกตัดทิ้ง ===")
    downloads.clear()
    h.MEDIA_GROUP_WAIT_SECONDS = 0.05
    await send_photo(20, CAP_WITH_AMOUNT, qr=["QR_A"], media_group_id="G1")
    await send_photo(21, "", qr=["QR_B"], media_group_id="G1")
    check("โหลดครบทุกใบ แม้ใบที่ไม่มี caption", sorted(downloads), [20, 21])

    collected = []
    real = h.process_slip_group
    h.process_slip_group = lambda *a: collected.append(a) or asyncio.sleep(0)
    await asyncio.sleep(0.3)
    check("  ประมวลผลเป็นชุดเดียว", len(collected), 1)
    check("  ได้ QR ครบทั้ง 2 ใบ", collected[0][4], ["QR_A", "QR_B"])
    check("  ใช้ caption จากใบที่มี", "benz4455" in collected[0][3], True)
    h.process_slip_group = real

    print("\n=== อัลบั้มที่ไม่มี caption เลยสักใบ -> เมินทั้งชุด ===")
    downloads.clear()
    await send_photo(30, "", qr=["QR_C"], media_group_id="G2")
    await send_photo(31, "", qr=["QR_D"], media_group_id="G2")
    check("โหลดไฟล์ไปแล้ว (ตัดตั้งแต่แรกไม่ได้)", sorted(downloads), [30, 31])
    await asyncio.sleep(0.3)
    check("  แต่ไม่สร้างรายการใน DB", txn_by_msg(30), None)
    with SessionLocal() as db:
        check("  ไม่มีรายการจากอัลบั้มนี้เลย",
              db.query(Transaction).filter(Transaction.msg_id.in_(["30", "31"])).count(), 0)

    print("\n=== ตัวช่วย ===")
    check("ข้อความว่าง", h.has_usable_caption(""), False)
    check("  None", h.has_usable_caption(None), False)
    check("  ช่องว่างล้วน", h.has_usable_caption("  \n\t "), False)
    check("  มีตัวอักษร", h.has_usable_caption("-maryann"), True)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
