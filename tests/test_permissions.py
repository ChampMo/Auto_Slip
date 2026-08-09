"""ทดสอบสิทธิ์การกดปุ่ม, audit ว่าใครกด และชื่อผู้อนุมัติในข้อความ"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "perm.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = " 111 , 222 ,, 333 "   # เว้นวรรค/คั่นเกินต้องไม่พัง

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
from core.config import config  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction, AuditLog  # noqa: E402

init_db()
h.append_to_sheet = lambda entry: (True, "")

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeUser:
    def __init__(self, user_id, username=None, full_name=None):
        self.id = user_id
        self.username = username
        self.full_name = full_name


class _FakeMessage:
    """แทน query.message — บอทใช้ตอบคำถามเพิ่มเติม เช่นขอเวลาโอน"""

    def __init__(self):
        self.chat_id = -100111
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        return type("Sent", (), {"message_id": 900 + len(self.replies)})()


class FakeQuery:
    def __init__(self, data, user):
        self.data = data
        self.from_user = user
        self.message = _FakeMessage()
        self.alerts = []
        self.texts = []
        self.answer_count = 0

    async def answer(self, text=None, show_alert=False):
        self.answer_count += 1
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.texts.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        pass

    @property
    def last_text(self):
        return self.texts[-1] if self.texts else ""


APPROVER = FakeUser(111, username="maryann")
OUTSIDER = FakeUser(999, username="randomguy")
NO_NAME = FakeUser(222)


async def press(data, user):
    query = FakeQuery(data, user)
    await h.button_callback(SimpleNamespace(callback_query=query), SimpleNamespace(bot=MagicMock()))
    return query


def new_slip(batch_id, receiver_account=None):
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.query(AuditLog).filter(AuditLog.qr_ref == batch_id).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status="pending",
            chat_id="-100111", msg_id="1", chat_amount=400.0,
            chat_trans_id="0001", receiver_account=receiver_account,
            # อ่านเวลาจากสลิปได้แล้ว เทสต์นี้ไม่ได้ทดสอบด่านถามเวลา
            transfer_time_text="0:18",
        ))
        db.commit()


def audit_of(batch_id):
    with SessionLocal() as db:
        return [
            (log.action, log.actor)
            for log in db.query(AuditLog).filter(AuditLog.qr_ref == batch_id).order_by(AuditLog.id)
        ]


def status_of(batch_id):
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        return txn.status if txn else None


async def main():
    print("=== อ่านรายชื่อจาก .env ===")
    check("ตัดช่องว่างและค่าว่างทิ้ง", sorted(config.SLIP_APPROVER_IDS), ["111", "222", "333"])
    check("คนในรายชื่อผ่าน", h.is_approver(APPROVER), True)
    check("  คนนอกไม่ผ่าน", h.is_approver(OUTSIDER), False)
    check("  ไม่มี user ไม่ผ่าน", h.is_approver(None), False)

    print("\n=== คนนอกกดปุ่ม ===")
    new_slip("p1", receiver_account="KKP-LS")
    q = await press("receive_p1", OUTSIDER)
    check("ขึ้น popup เตือน", "not allowed to approve" in q.alerts[0], True)
    check("  ไม่แก้ข้อความในกลุ่ม", q.texts, [])
    check("  สถานะไม่เปลี่ยน", status_of("p1"), "pending")
    check("  ไม่มี audit log", audit_of("p1"), [])

    print("\n=== คนนอกแอบกดปุ่มเลือกธนาคาร (ทางลัดที่คนมักลืมปิด) ===")
    q = await press("bank_p1_SCB-CP", OUTSIDER)
    check("ถูกบล็อกเหมือนกัน", "not allowed to approve" in q.alerts[0], True)
    check("  ไม่ถูกเขียนลงชีท", status_of("p1"), "pending")
    q = await press("back_p1", OUTSIDER)
    check("  ปุ่ม Back ก็ถูกบล็อก", "not allowed to approve" in q.alerts[0], True)

    print("\n=== คนในรายชื่อกด Receive ===")
    q = await press("receive_p1", APPROVER)
    check("บันทึกสำเร็จ", "✅ Received" in q.last_text, True)
    check("  มีชื่อผู้อนุมัติในข้อความ", "Approved by: @maryann" in q.last_text, True)
    check("  สถานะเป็น Receive", status_of("p1"), "Receive")
    check("  ตอบ callback ครั้งเดียว", q.answer_count, 1)

    actions = audit_of("p1")
    check("  audit เก็บครบทุกขั้น", [a for a, _ in actions],
          ["bank_taken_from_slip", "saving_started", "sheet_saved"])
    check("  ทุกขั้นรู้ว่าใครกด", {actor for _, actor in actions}, {"111 @maryann"})

    print("\n=== คนในรายชื่อกด Reject ===")
    new_slip("p2")
    q = await press("reject_p2", APPROVER)
    check("ปฏิเสธสำเร็จ", "❌ Rejected" in q.last_text, True)
    check("  มีชื่อคนปฏิเสธ", "Rejected by: @maryann" in q.last_text, True)
    check("  audit เก็บคนกด", audit_of("p2"), [("admin_rejected", "111 @maryann")])

    print("\n=== เลือกธนาคารเอง ===")
    new_slip("p3")
    await press("receive_p3", APPROVER)          # ยังไม่รู้บัญชี -> ขึ้นปุ่มเลือก
    q = await press("bank_p3_SCB-CP", APPROVER)
    check("บันทึกด้วยบัญชีที่เลือก", "Account: SCB-CP" in q.last_text, True)
    check("  มีชื่อผู้อนุมัติ", "Approved by: @maryann" in q.last_text, True)
    check("  audit บอกว่าเลือกเอง", [a for a, _ in audit_of("p3")][0], "manual_bank_selected")

    print("\n=== คนที่ไม่มี username -> ใช้ id แทน ===")
    new_slip("p4")
    q = await press("reject_p4", NO_NAME)
    check("ผ่านเพราะ id อยู่ในรายชื่อ", "❌ Rejected" in q.last_text, True)
    check("  แสดงเป็น id", "Rejected by: id 222" in q.last_text, True)
    check("  audit เก็บ id", audit_of("p4"), [("admin_rejected", "222 id 222")])

    print("\n=== ไม่ได้ตั้งรายชื่อเลย -> ห้ามทุกคน ===")
    original = config.SLIP_APPROVER_IDS
    config.SLIP_APPROVER_IDS = frozenset()
    new_slip("p5", receiver_account="KKP-LS")
    q = await press("receive_p5", APPROVER)
    check("แม้แต่คนที่เคยมีสิทธิ์ก็กดไม่ได้", "No one is allowed to approve" in q.alerts[0], True)
    check("  บอกวิธีแก้", "approver list" in q.alerts[0], True)
    check("  ไม่มีอะไรถูกบันทึก", status_of("p5"), "pending")
    config.SLIP_APPROVER_IDS = original

    print("\n=== ชื่อที่ใช้แสดงผล ===")
    check("มี username", h.describe_user(FakeUser(1, username="bee")), "@bee")
    check("  ไม่มี username แต่มีชื่อ", h.describe_user(FakeUser(2, full_name="Lara T.")), "Lara T.")
    check("  ไม่มีอะไรเลย", h.describe_user(FakeUser(3)), "id 3")
    check("  actor ติด id เสมอ", h.describe_actor(FakeUser(4, username="x")), "4 @x")


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
