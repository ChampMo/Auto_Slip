"""ทดสอบการจัดการรายชื่อผู้อนุมัติผ่านคำสั่ง"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "approver.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111"      # เจ้าของคนเดียวใน .env
os.environ["BOT_TOKEN"] = "test-token"

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

import bot.commands as cmds  # noqa: E402
import bot.handlers as h  # noqa: E402
from core.config import config  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Approver, AuditLog  # noqa: E402

init_db()
failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeUser:
    def __init__(self, user_id, username=None, full_name=None, is_bot=False):
        self.id = user_id
        self.username = username
        self.full_name = full_name
        self.is_bot = is_bot


class FakeMessage:
    def __init__(self, reply_to=None):
        self.reply_to_message = reply_to
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)

    @property
    def last(self):
        return self.replies[-1] if self.replies else ""


OWNER = FakeUser(111, username="owner")
STAFF = FakeUser(222, username="maryann", full_name="Mary Ann")
NEWBIE = FakeUser(333, username="somchai", full_name="Somchai J")
OUTSIDER = FakeUser(999, username="stranger")
A_BOT = FakeUser(777, username="somebot", is_bot=True)


class FakeBot:
    """แทน Telegram — ใช้หาชื่อจากเลข id ของเจ้าของที่ตั้งไว้ในไฟล์ตั้งค่า"""

    def __init__(self, known=None, fail=False):
        self.known = known or {}
        self.fail = fail
        self.asked = []

    async def get_chat(self, user_id):
        self.asked.append(user_id)
        if self.fail:
            raise RuntimeError("Telegram หาไม่เจอ")
        found = self.known.get(int(user_id))
        if found is None:
            raise RuntimeError("ไม่รู้จัก user นี้")
        return found


DEFAULT_BOT = FakeBot({111: FakeUser(111, username="owner", full_name="Owner P")})


async def run(command, actor, reply_to_user=None, args=None, bot=None):
    replied = SimpleNamespace(from_user=reply_to_user) if reply_to_user else None
    message = FakeMessage(reply_to=replied)
    update = SimpleNamespace(effective_message=message, effective_user=actor)
    context = SimpleNamespace(args=args or [], bot=bot or DEFAULT_BOT)
    await command(update, context)
    return message


def approver_ids_in_db():
    with SessionLocal() as db:
        return sorted(row.user_id for row in db.query(Approver).all())


def audit_for(user_id):
    with SessionLocal() as db:
        return [
            (log.action, log.actor)
            for log in db.query(AuditLog).filter(AuditLog.qr_ref == f"approver:{user_id}").order_by(AuditLog.id)
        ]


async def main():
    print("=== เริ่มต้น: มีแต่เจ้าของใน .env ===")
    check("เจ้าของอนุมัติได้", h.is_approver(OWNER), True)
    check("  คนอื่นยังไม่ได้", h.is_approver(STAFF), False)
    check("  DB ยังว่าง", approver_ids_in_db(), [])

    print("\n=== เจ้าของเพิ่มคนใหม่ด้วยการ reply ===")
    msg = await run(cmds.approver_add_command, OWNER, reply_to_user=STAFF)
    check("เพิ่มสำเร็จ", "✅ Added @maryann" in msg.last, True)
    check("  อยู่ใน DB แล้ว", approver_ids_in_db(), ["222"])
    check("  อนุมัติได้ทันที", h.is_approver(STAFF), True)
    check("  audit บันทึกว่าใครเพิ่ม", audit_for(222), [("approver_added", "111 @owner")])

    print("\n=== คนที่เพิ่งถูกเพิ่ม เพิ่มคนต่อได้ (ตามที่เลือกไว้) ===")
    msg = await run(cmds.approver_add_command, STAFF, reply_to_user=NEWBIE)
    check("เพิ่มได้", "✅ Added @somchai" in msg.last, True)
    check("  audit บอกว่า maryann เป็นคนเพิ่ม", audit_for(333), [("approver_added", "222 @maryann")])

    print("\n=== คนนอกทำอะไรไม่ได้ ===")
    msg = await run(cmds.approver_add_command, OUTSIDER, reply_to_user=OUTSIDER)
    check("เพิ่มไม่ได้", "not allowed to manage" in msg.last, True)
    check("  ไม่มีอะไรถูกเพิ่ม", approver_ids_in_db(), ["222", "333"])
    msg = await run(cmds.approvers_command, OUTSIDER)
    check("  ดูรายชื่อก็ไม่ได้", "not allowed to manage" in msg.last, True)

    print("\n=== เพิ่มด้วยเลข id ตรงๆ ===")
    msg = await run(cmds.approver_add_command, OWNER, args=["444"])
    check("เพิ่มด้วย id ได้", "id 444" in msg.last, True)
    check("  อยู่ใน DB", "444" in approver_ids_in_db(), True)

    print("\n=== เพิ่มซ้ำ / เพิ่มบอท / ใส่ @username ===")
    msg = await run(cmds.approver_add_command, OWNER, reply_to_user=STAFF)
    check("เพิ่มซ้ำ", "can already approve" in msg.last, True)
    msg = await run(cmds.approver_add_command, OWNER, reply_to_user=A_BOT)
    check("  เพิ่มบอทไม่ได้", "That is a bot" in msg.last, True)
    msg = await run(cmds.approver_add_command, OWNER, args=["@somchai"])
    check("  @username ใช้ไม่ได้ พร้อมบอกเหตุผล", "does not let bots look up an ID" in msg.last, True)
    msg = await run(cmds.approver_add_command, OWNER)
    check("  ไม่ reply ไม่ใส่ id -> บอกวิธีใช้", "Reply to a message" in msg.last, True)

    print("\n=== เพิ่มเจ้าของที่อยู่ใน .env อยู่แล้ว ===")
    msg = await run(cmds.approver_add_command, OWNER, reply_to_user=OWNER)
    check("บอกว่าเป็นเจ้าของอยู่แล้ว", "already an owner" in msg.last, True)

    print("\n=== ดูรายชื่อ ===")
    msg = await run(cmds.approvers_command, OWNER)
    check("แสดงจำนวนที่เพิ่มผ่านคำสั่ง", "Added here with /approver_add: 3" in msg.last, True)
    check("  มีชื่อ maryann", "@maryann" in msg.last, True)
    check("  บอกจำนวนเจ้าของจาก config", "Owners from config: 1" in msg.last, True)

    print("\n=== เจ้าของใน .env ต้องแสดงตัวตน ไม่ใช่แค่จำนวน ===")
    check("โชว์ชื่อเจ้าของ ไม่ใช่แค่ตัวเลข", "@owner (id 111)" in msg.last, True)
    check("  บอกว่าเป็นตัวเอง", "@owner (id 111) — you" in msg.last, True)

    # คนอื่นเรียกดู ต้องไม่ขึ้นว่า you
    msg_other = await run(cmds.approvers_command, STAFF)
    check("  คนอื่นเรียกดู ไม่ขึ้น you", "— you" in msg_other.last, False)
    check("  แต่ยังเห็นชื่อเจ้าของ", "@owner (id 111)" in msg_other.last, True)

    print("\n=== ถาม Telegram ไม่ได้ ต้องไม่พัง ===")
    msg_fail = await run(cmds.approvers_command, OWNER, bot=FakeBot(fail=True))
    check("ยังตอบได้", "Owners from config: 1" in msg_fail.last, True)
    check("  แสดงเลข id แทนชื่อ", "• id 111" in msg_fail.last, True)

    print("\n=== ถอดออกจากรายชื่อ ===")
    msg = await run(cmds.approver_remove_command, STAFF, reply_to_user=NEWBIE)
    check("ถอดได้", "🚫 Removed @somchai" in msg.last, True)
    check("  หายจาก DB", "333" in approver_ids_in_db(), False)
    check("  อนุมัติไม่ได้แล้ว", h.is_approver(NEWBIE), False)
    check("  audit บันทึกการถอด", audit_for(333)[-1], ("approver_removed", "222 @maryann"))

    msg = await run(cmds.approver_remove_command, OWNER, reply_to_user=NEWBIE)
    check("ถอดคนที่ไม่อยู่ในรายชื่อ", "is not in the approver list" in msg.last, True)

    print("\n=== ถอดเจ้าของใน .env ไม่ได้ ===")
    msg = await run(cmds.approver_remove_command, STAFF, reply_to_user=OWNER)
    check("ปฏิเสธ พร้อมบอกวิธีแก้", "cannot be removed with a command" in msg.last, True)
    check("  เจ้าของยังอนุมัติได้", h.is_approver(OWNER), True)

    print("\n=== ถอดตัวเองได้ (ยังมีเจ้าของใน .env เป็นกุญแจสำรอง) ===")
    msg = await run(cmds.approver_remove_command, STAFF, reply_to_user=STAFF)
    check("ถอดตัวเองสำเร็จ", "🚫 Removed @maryann" in msg.last, True)
    check("  กดปุ่มไม่ได้แล้ว", h.is_approver(STAFF), False)
    check("  เจ้าของยังเข้าได้ ไม่ล็อกเอาต์", h.is_approver(OWNER), True)

    print("\n=== ไม่มีเจ้าของใน .env -> ห้ามถอดคนสุดท้าย ===")
    original = config.SLIP_APPROVER_IDS
    config.SLIP_APPROVER_IDS = frozenset()
    with SessionLocal() as db:
        db.query(Approver).delete()
        db.add(Approver(user_id="555", username="lastone", added_by="test"))
        db.commit()

    last_one = FakeUser(555, username="lastone")
    msg = await run(cmds.approver_remove_command, last_one, reply_to_user=last_one)
    check("กันล็อกเอาต์", "would lock everyone out" in msg.last, True)
    check("  ยังอยู่ในรายชื่อ", approver_ids_in_db(), ["555"])
    config.SLIP_APPROVER_IDS = original

    print("\n=== /myid ===")
    msg = await run(cmds.myid_command, STAFF)
    check("บอก id ของตัวเอง", "Your Telegram ID: 222" in msg.last, True)
    check("  ใครก็ใช้ได้ ไม่ต้องมีสิทธิ์", "Your Telegram ID: 999" in (await run(cmds.myid_command, OUTSIDER)).last, True)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
