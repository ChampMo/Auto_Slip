"""ทดสอบคำสั่งดูสถานะ /status /pending /today /health /help"""
import asyncio
import datetime as dt
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

DB_PATH = os.path.join(tempfile.mkdtemp(), "status.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-1004418034373"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111"
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
from database.crud import add_audit_log  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction  # noqa: E402

init_db()
BKK = ZoneInfo("Asia/Bangkok")
WE_CHAT = "-1004418034373"
failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeUser:
    def __init__(self, user_id, username=None):
        self.id = user_id
        self.username = username
        self.full_name = username


class FakeMessage:
    def __init__(self, chat_id, reply_to_id=None):
        self.chat_id = chat_id
        self.reply_to_message = SimpleNamespace(message_id=reply_to_id) if reply_to_id else None
        self.replies = []

    async def reply_text(self, text, **kwargs):
        sent = FakeMessage(self.chat_id)
        sent.text = text
        self.replies.append(text)
        sent.edits = []

        async def edit_text(new_text, **kw):
            sent.edits.append(new_text)
            self.replies.append(new_text)

        sent.edit_text = edit_text
        return sent

    @property
    def last(self):
        return self.replies[-1] if self.replies else ""


APPROVER = FakeUser(111, "owner")
OUTSIDER = FakeUser(999, "stranger")


async def run(command, actor, chat_id=WE_CHAT, reply_to_id=None, args=None, app=None):
    message = FakeMessage(chat_id, reply_to_id)
    update = SimpleNamespace(effective_message=message, effective_user=actor)
    context = SimpleNamespace(args=args or [], application=app or SimpleNamespace(bot_data={}))
    await command(update, context)
    return message


def inside_today():
    """เวลาที่อยู่ในวันธุรกิจปัจจุบันแน่นอน และไม่ใช่อนาคต

    ห้ามใช้ "ย้อนหลัง N นาที" กับสลิปที่ /today ต้องนับ เพราะวันธุรกิจเปลี่ยนตอน 5 ทุ่ม
    ถ้ารันเทสต์ในช่วงหลังเที่ยงคืนไม่นาน เวลาที่ย้อนไปจะตกไปอยู่วันก่อนหน้า
    แล้วเทสต์จะพังเฉพาะบางช่วงเวลาของวัน ซึ่งหาสาเหตุยากมาก
    """
    start, _ = cmds.business_day_range_utc()
    return start + (dt.datetime.utcnow() - start) / 2


def utc_ago(minutes):
    return dt.datetime.utcnow() - dt.timedelta(minutes=minutes)


def make_slip(batch_id, msg_id, status, chat_id=WE_CHAT, created=None, **kw):
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status=status,
            chat_id=chat_id, msg_id=str(msg_id),
            created_at=created or dt.datetime.utcnow(), **kw,
        ))
        db.commit()


async def main():
    print("=== เตรียมข้อมูล ===")
    make_slip("b_ok", 500, "Receive", chat_trans_id="0000123", chat_fullname="ดุลยฤทธิ์",
              chat_amount=400.0, api_total_amount=400.0, sender_names="นาย ดุลยฤทธิ์ ส",
              receiver_account="KKP-LS", transfer_time_text="0:18", created=inside_today())
    make_slip("b_wait", 501, "pending", chat_trans_id="0000124", chat_amount=1000.0,
              created=inside_today())
    make_slip("b_stuck", 502, "interrupted", chat_user_id="benz4455", chat_amount=250.0,
              created=inside_today())
    make_slip("b_no", 503, "Reject", chat_trans_id="0000125", chat_amount=900.0,
              created=inside_today())
    with SessionLocal() as db:
        add_audit_log(db, "b_ok", "manual_review_required")
        add_audit_log(db, "b_ok", "bank_taken_from_slip", actor="111 @owner")
        add_audit_log(db, "b_ok", "sheet_saved", actor="111 @owner")

    print("\n=== /status ด้วยการ reply ===")
    msg = await run(cmds.status_command, APPROVER, reply_to_id=500)
    check("บอกสถานะ", "✅ Received" in msg.last, True)
    check("  แสดงข้อมูลที่แชทแจ้ง", "ID: 0000123" in msg.last and "400.00 THB" in msg.last, True)
    check("  แสดงข้อมูลจากสลิป", "นาย ดุลยฤทธิ์ ส" in msg.last and "KKP-LS" in msg.last, True)
    # เทียบสลิปกับแชทให้เห็นเหมือนตอนที่บอทถามในกลุ่ม ไม่ใช่แค่ทิ้งค่าไว้สองบรรทัด
    check("  กางผลตรวจแบบเดียวกับตอนถาม", "✅ Name" in msg.last and "✅ Amount" in msg.last
          and "✅ Account KKP-LS" in msg.last, True)
    # เวลาโอนคือเวลาที่เงินออกจริง คนละอย่างกับเวลาที่กดบันทึก
    check("  แสดงเวลาโอนจากสลิป", "Transferred at: 0:18" in msg.last, True)
    # ใบเก่าที่บันทึกไว้ก่อนระบบเก็บเวลา ต้องไม่ขึ้นบรรทัดนี้ค้างไว้เฉยๆ
    old_slip = await run(cmds.status_command, APPROVER, args=["0000124"])
    check("  ใบที่ไม่มีเวลาโอน ไม่ขึ้นบรรทัดนี้", "Transferred at" in old_slip.last, False)
    check("  มีประวัติ", "History:" in msg.last, True)
    check("  แปลชื่อ action เป็นภาษาคน", "Saved to the sheet" in msg.last, True)
    check("  บอกว่าใครทำ", "@owner" in msg.last, True)

    print("\n=== /status ด้วย Trans ID ===")
    msg = await run(cmds.status_command, APPROVER, args=["0000124"])
    check("ค้นด้วย ID ได้", "⏳ Waiting" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER, args=["benz4455"])
    check("  ค้นด้วย User ได้", "Interrupted" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER, args=["ไม่มีจริง"])
    check("  ไม่เจอ", "No slip found for ID" in msg.last, True)

    print("\n=== /status กรณีใช้ผิดวิธี ===")
    msg = await run(cmds.status_command, APPROVER, reply_to_id=99999)
    check("reply ผิดใบ -> แนะนำวิธี", "carries the caption" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER)
    check("  ไม่ reply ไม่ใส่ ID", "Reply to the slip photo" in msg.last, True)

    print("\n=== /status สิทธิ์ ===")
    msg = await run(cmds.status_command, OUTSIDER, reply_to_id=500)
    check("คนทั่วไปในกลุ่มที่ลงทะเบียนดูได้", "✅ Received" in msg.last, True)
    msg = await run(cmds.status_command, OUTSIDER, chat_id="-100999", reply_to_id=500)
    check("  นอกกลุ่ม คนนอกดูไม่ได้", "not allowed to look up" in msg.last, True)

    print("\n=== ลิงก์กลับไปข้อความเดิม + batch id ===")
    msg = await run(cmds.status_command, APPROVER, reply_to_id=500)
    check("มีลิงก์ไปข้อความต้นทาง",
          "Original message: https://t.me/c/4418034373/500" in msg.last, True)
    check("  โชว์ batch ไว้ให้เอาไปใช้ต่อ", "Batch: b_ok" in msg.last, True)

    print("\n=== /status -b ค้นด้วย batch id ===")
    msg = await run(cmds.status_command, APPROVER, args=["-b", "b_ok"])
    check("ค้นด้วย batch เต็มได้", "✅ Received" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER, args=["-b", "b_o"])
    check("  ค้นด้วย batch แบบย่อได้", "✅ Received" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER, args=["--batch", "b_wait"])
    check("  ใช้ --batch ก็ได้", "⏳ Waiting" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER, args=["-b"])
    check("  ไม่ใส่เลข -> บอกวิธี", "Pass the batch id too" in msg.last, True)
    msg = await run(cmds.status_command, APPROVER, args=["-b", "ไม่มีจริง"])
    check("  ไม่เจอ", "No slip found for batch" in msg.last, True)
    # "b_" ขึ้นต้นตรงกับหลายใบ ต้องไม่เดาให้
    msg = await run(cmds.status_command, APPROVER, args=["-b", "b_"])
    check("  ย่อสั้นจนซ้ำ -> ไม่เดา", "More than one slip" in msg.last, True)

    print("\n=== reply ที่สลิปที่ส่งซ้ำ ต้องยังหาเจอ ===")
    # สลิปซ้ำไม่ถูกบันทึกเป็นรายการใหม่ ข้อความนั้นจึงไม่มีรายการผูกอยู่
    # ต้องอ่านรหัสจาก caption ของข้อความนั้นแล้วค้นใบแรกให้แทน
    resent = SimpleNamespace(message_id=77777, caption="TRANS ID : 0000123\nAMOUNT THB : 400.00")
    message = FakeMessage(WE_CHAT)
    message.reply_to_message = resent
    await cmds.status_command(
        SimpleNamespace(effective_message=message, effective_user=APPROVER),
        SimpleNamespace(args=[]),
    )
    check("หาใบแรกเจอจาก caption", "✅ Received" in message.last, True)
    check("  ไม่ขึ้นว่าไม่มีรายการผูก", "No slip is linked" in message.last, False)

    # caption ที่ไม่มีรหัสเลย ยังต้องบอกวิธีใช้ตามเดิม
    blank = SimpleNamespace(message_id=77778, caption="ช่วยเช็คให้หน่อย")
    message = FakeMessage(WE_CHAT)
    message.reply_to_message = blank
    await cmds.status_command(
        SimpleNamespace(effective_message=message, effective_user=APPROVER),
        SimpleNamespace(args=[]),
    )
    check("  caption ไม่มีรหัส -> แนะนำวิธี", "No slip is linked" in message.last, True)

    print("\n=== /pending ===")
    msg = await run(cmds.pending_command, APPROVER)
    check("นับเฉพาะที่ยังค้าง", "⏳ 2 slip(s) waiting" in msg.last, True)
    check("  มีใบที่รออยู่", "0000124" in msg.last, True)
    check("  มีใบที่ค้างจากบอทดับ พร้อมคำเตือน", "must be sent again" in msg.last, True)
    check("  ไม่เอาใบที่จบแล้ว", "0000123" in msg.last, False)
    check("  มีลิงก์กระโดดไปข้อความ", "https://t.me/c/4418034373/501" in msg.last, True)
    msg = await run(cmds.pending_command, OUTSIDER)
    check("  คนนอกดูไม่ได้", "Only approvers" in msg.last, True)

    print("\n=== /today ===")
    msg = await run(cmds.today_command, APPROVER)
    check("นับของวันนี้", "Received  1" in msg.last, True)
    check("  รวมยอด", "400.00 THB" in msg.last, True)
    check("  นับที่ถูกปฏิเสธ", "Rejected  1" in msg.last, True)
    check("  นับที่ยังค้าง", "Waiting   2" in msg.last, True)
    check("  บอกไฟล์กับแท็บปลายทาง", "Deposit-" in msg.last and "check_" in msg.last, True)

    print("\n=== /today ใช้ปฏิทินธุรกิจ วันเปลี่ยนตอน 5 ทุ่มไทย ===")
    # 3 โมงเช้าเวลาไทย ต้องถูกนับเป็น "วันนี้" เหมือนเดิม
    now_bkk = dt.datetime.now(BKK).replace(hour=3, minute=0, second=0, microsecond=0)
    start_utc, end_utc = cmds.business_day_range_utc(now_bkk)
    early_morning_utc = now_bkk.astimezone(dt.timezone.utc).replace(tzinfo=None)
    check("ช่วงเวลาครอบสลิปตอนตี 3", start_utc <= early_morning_utc < end_utc, True)
    check("  ช่วงยาว 24 ชั่วโมง", (end_utc - start_utc), dt.timedelta(days=1))
    check("  เริ่มที่ 16:00 UTC (= 23:00 ไทย)", start_utc.hour, 16)

    # สลิปตอน 23:30 ไทย ต้องเป็นของ "พรุ่งนี้" ไม่ใช่วันนี้
    late_night = now_bkk.replace(hour=23, minute=30)
    late_utc = late_night.astimezone(dt.timezone.utc).replace(tzinfo=None)
    check("  สลิป 23:30 ไทย ไม่นับเป็นวันนี้", start_utc <= late_utc < end_utc, False)
    # ส่วน 22:30 ยังเป็นของวันนี้อยู่
    before = now_bkk.replace(hour=22, minute=30)
    before_utc = before.astimezone(dt.timezone.utc).replace(tzinfo=None)
    check("  สลิป 22:30 ไทย ยังนับเป็นวันนี้", start_utc <= before_utc < end_utc, True)

    print("\n=== /health ===")
    cmds.collect_sheet_health = lambda: {
        "folder": "OK — Deposit-2026",
        "file": "OK — check_08-2026",
        "tab": "OK — 04-08-2026",
        "next": "not created yet — Deposit-2026/check_09-2026",
    }
    fake_job = SimpleNamespace(
        id="nightly", name="Create tomorrow's tab",
        next_run_time=dt.datetime.now(BKK) + dt.timedelta(hours=3),
    )
    app = SimpleNamespace(bot_data={"scheduler": SimpleNamespace(get_jobs=lambda: [fake_job])})
    msg = await run(cmds.health_command, APPROVER, app=app)
    check("ตอบว่ากำลังตรวจก่อน", "Checking" in msg.replies[0], True)
    check("  DB ปกติ", "Database:     OK" in msg.last, True)
    check("  บอกสถานะไฟล์เดือนนี้", "check_08-2026" in msg.last, True)
    check("  เตือนไฟล์เดือนหน้า", "not created yet" in msg.last, True)
    check("  นับผู้อนุมัติ", "Approvers:    1" in msg.last, True)
    check("  แสดงงานที่ตั้งเวลาไว้", "Create tomorrow's tab" in msg.last, True)

    msg = await run(cmds.health_command, APPROVER, app=SimpleNamespace(bot_data={}))
    check("ไม่มี scheduler -> บอกตรงๆ", "scheduler is not running" in msg.last, True)
    msg = await run(cmds.health_command, OUTSIDER)
    check("  คนนอกใช้ไม่ได้", "Only approvers" in msg.last, True)

    print("\n=== /help ===")
    msg = await run(cmds.help_command, OUTSIDER)
    check("ใครก็ดูได้", "/status" in msg.last, True)
    check("  แยกส่วนที่ต้องมีสิทธิ์", "Approvers only:" in msg.last, True)
    for command in ["/pending", "/today", "/health", "/myid", "/approver_add"]:
        check(f"  มี {command}", command in msg.last, True)

    print("\n=== ตัวช่วย ===")
    check("ลิงก์ supergroup", cmds.message_link("-1004418034373", 99), "https://t.me/c/4418034373/99")
    check("  กลุ่มที่ไม่ใช่ supergroup -> ไม่มีลิงก์", cmds.message_link("-5153291438", 99), "")
    check("  อายุเป็นนาที", cmds.describe_age(utc_ago(25)), "25 min ago")
    check("  อายุเป็นชั่วโมง", cmds.describe_age(utc_ago(200)), "3 hr ago")
    check("  เพิ่งเกิด", cmds.describe_age(utc_ago(0)), "just now")


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
