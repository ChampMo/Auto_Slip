"""ทดสอบการแย่งกันกดปุ่ม ด้วย button_callback ตัวจริงบน sqlite จริง"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "race.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111,222"   # จำลองแอดมิน 2 คนที่มีสิทธิ์กด

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
from database.crud import claim_transaction  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction  # noqa: E402

init_db()

failed = 0
sheet_writes = []


def fake_append_to_sheet(txn):
    sheet_writes.append({"batch_id": txn.batch_id, "bank": txn.receiver_account})
    return True, ""


h.append_to_sheet = fake_append_to_sheet


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeUser:
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username
        self.full_name = username


ADMIN_A = FakeUser(111, "adminA")
ADMIN_B = FakeUser(222, "adminB")


class _FakeMessage:
    """แทน query.message — บอทใช้ตอบคำถามเพิ่มเติม เช่นขอเวลาโอน"""

    def __init__(self):
        self.chat_id = -100111
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        return type("Sent", (), {"message_id": 900 + len(self.replies)})()


class FakeQuery:
    """แทน CallbackQuery ของ Telegram เก็บทุกอย่างที่บอทตอบกลับ"""

    def __init__(self, data, user=ADMIN_A):
        self.data = data
        self.from_user = user
        self.message = _FakeMessage()
        self.alerts = []
        self.texts = []
        self.markups = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.texts.append(text)
        self.markups.append(reply_markup)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)

    @property
    def last_text(self):
        return self.texts[-1] if self.texts else ""


async def press(callback_data, user=ADMIN_A):
    query = FakeQuery(callback_data, user)
    await h.button_callback(
        SimpleNamespace(callback_query=query), SimpleNamespace(bot=MagicMock())
    )
    return query


def new_slip(batch_id, receiver_account=None):
    """สร้างรายการที่รอแอดมินตัดสิน"""
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status="pending",
            chat_id="-100111", msg_id="1", chat_amount=400.0,
            chat_trans_id="0001", receiver_account=receiver_account,
            # อ่านเวลาจากสลิปได้แล้ว เทสต์นี้ไม่ได้ทดสอบด่านถามเวลา
            transfer_time_text="0:18",
        ))
        db.commit()


def status_of(batch_id):
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        return txn.status if txn else None


def bank_of(batch_id):
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        return txn.receiver_account if txn else None


async def main():
    print("=== ช่องโหว่ 1: สองคนเลือกธนาคารคนละอัน ===")
    sheet_writes.clear()
    new_slip("race1")
    await press("receive_race1", ADMIN_A)             # A กด Receive -> ขึ้นปุ่มเลือกธนาคาร
    a = await press("bank_race1_KKP-LS", ADMIN_A)     # A เลือก KKP-LS
    b = await press("bank_race1_SCB-CP", ADMIN_B)     # B เลือก SCB-CP ตามมาติดๆ
    check("A บันทึกสำเร็จ", "✅ Received" in a.last_text, True)
    check("  B ถูกปฏิเสธ", "Already received" in b.last_text, True)
    check("  เขียนชีทครั้งเดียว", len(sheet_writes), 1)
    check("  ชีทได้ธนาคารของ A", sheet_writes[0]["bank"], "KKP-LS")
    check("  DB ไม่ถูกเขียนทับเป็นของ B", bank_of("race1"), "KKP-LS")

    print("\n=== ช่องโหว่ 2: Reject แล้ว แต่ปุ่มธนาคารยังค้างบนจออีกคน ===")
    sheet_writes.clear()
    new_slip("race2")
    await press("receive_race2", ADMIN_A)             # A กด Receive -> ได้ปุ่มธนาคาร
    r = await press("reject_race2", ADMIN_B)          # B กด Reject ตัดหน้า
    late = await press("bank_race2_KKP-LS", ADMIN_A)  # A เพิ่งกดธนาคารจากปุ่มที่ค้างอยู่
    check("B ปฏิเสธสำเร็จ", "❌ Rejected" in r.last_text, True)
    check("  ปุ่มธนาคารที่ค้างถูกบล็อก", "Already rejected" in late.last_text, True)
    check("  ไม่มีการเขียนลงชีทเลย", len(sheet_writes), 0)
    check("  สถานะยังเป็น Reject", status_of("race2"), "Reject")

    print("\n=== กดปุ่มเดิมรัวๆ (บัญชีรู้อยู่แล้ว) ===")
    sheet_writes.clear()
    new_slip("race3", receiver_account="KKP-LS")
    first = await press("receive_race3")
    second = await press("receive_race3")
    third = await press("receive_race3")
    check("ครั้งแรกบันทึก", "✅ Received" in first.last_text, True)
    check("  ครั้งที่ 2 ถูกบล็อก", "Already received" in second.last_text, True)
    check("  ครั้งที่ 3 ถูกบล็อก", "Already received" in third.last_text, True)
    check("  เขียนชีทครั้งเดียว", len(sheet_writes), 1)

    print("\n=== Reject ชนกับ Receive ===")
    sheet_writes.clear()
    new_slip("race4", receiver_account="KKP-LS")
    rej = await press("reject_race4")
    rec = await press("receive_race4")
    check("Reject มาก่อน ชนะ", status_of("race4"), "Reject")
    check("  Receive ที่ตามมาถูกบล็อก", "Already rejected" in rec.last_text, True)
    check("  ไม่มีการเขียนลงชีท", len(sheet_writes), 0)
    check("  ข้อความ Reject ถูกต้อง", "❌ Rejected" in rej.last_text, True)

    print("\n=== claim_transaction ระดับ DB ===")
    new_slip("claim1")
    with SessionLocal() as db:
        check("คนแรกจองได้", claim_transaction(db, "claim1", "Receive"), True)
        check("  คนที่สองจองไม่ได้", claim_transaction(db, "claim1", "Receive"), False)
        check("  ฝั่ง Reject ก็แทรกไม่ได้", claim_transaction(db, "claim1", "Reject"), False)
        check("  สถานะเป็นของคนแรก", status_of("claim1"), "Receive")

        # เขียนชีทไม่สำเร็จ -> ต้องกลับมาจองใหม่ได้
        txn = db.query(Transaction).filter(Transaction.batch_id == "claim1").first()
        h.unlock_after_failed_save(db, txn)
        check("  ปลดล็อกแล้วจองใหม่ได้", claim_transaction(db, "claim1", "Receive"), True)

    with SessionLocal() as db:
        check("batch ที่ไม่มีอยู่จริง จองไม่ได้", claim_transaction(db, "ไม่มีจริง", "Receive"), False)

    print("\n=== รายการที่หาไม่เจอ ===")
    missing = await press("receive_ไม่มีจริง")
    check("แจ้งว่าไม่พบรายการ", "no longer in the system" in missing.last_text, True)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
