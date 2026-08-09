"""ทดสอบว่าคอลัมน์ยาวพอกับข้อมูลจริง

เหตุผลที่ต้องมีเทสต์ชุดนี้แยก: เครื่องทดสอบใช้ SQLite ซึ่งไม่บังคับความยาวคอลัมน์
แต่ของจริงใช้ Postgres ซึ่งบังคับ ค่าที่ยาวเกินจะทำให้ INSERT ล้ม
แล้วสลิปใบนั้นหายไปเงียบๆ — เทสต์ปกติจับไม่ได้เลย จึงต้องเทียบกับ schema ตรงๆ
"""
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "limits.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"

for m in ["telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
          "google", "google.oauth2", "google.oauth2.service_account", "googleapiclient",
          "googleapiclient.discovery", "googleapiclient.errors", "requests",
          "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, MagicMock(name=m))
ge = types.ModuleType("gspread.exceptions")
ge.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
ge.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = ge

import database.models as models  # noqa: E402
from core.captions import extract_data_from_caption  # noqa: E402
from core.matcher import process_incoming_slip  # noqa: E402
from database.crud import add_audit_log, get_audit_trail  # noqa: E402
from database.models import Transaction, UsedQR  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402

init_db()
failed = 0

# ขีดจำกัดจริงของข้อมูลที่ระบบจะเจอ
TELEGRAM_CAPTION_MAX = 1024   # Telegram ไม่ยอมให้ caption ยาวกว่านี้
EMV_QR_MAX = 512              # QR แบบ EMV ยาวสุดประมาณนี้


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def column_length(model, name):
    return getattr(model.__table__.c[name].type, "length", None)


print("=== คอลัมน์ต้องยาวพอกับข้อมูลจริง ===")
for model, column, needed, why in [
    (Transaction, "raw_caption", TELEGRAM_CAPTION_MAX, "caption ยาวสุดของ Telegram"),
    (UsedQR, "qr_ref", EMV_QR_MAX, "QR payload แบบ EMV"),
]:
    length = column_length(model, column)
    ok = length is not None and length >= needed
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {column}: คอลัมน์ {length} ต้องรับได้ {needed} ({why})")

print("\n=== ค่าที่ระบบสร้างเอง ต้องไม่ยาวเกินคอลัมน์ ===")
# ข้อความเตือนมีบรรทัดจาก caption แปะอยู่ ยาวตามความยาวบรรทัดนั้น
long_line = "Amount : " + "x" * 300
note = extract_data_from_caption(long_line)["amount_note"] or ""
warn_limit = column_length(Transaction, "caption_warning")
check("ข้อความเตือนไม่ยาวเกินคอลัมน์ (หลังตัดแล้ว)",
      len(models.clamp(note, models.WARNING_MAX)) <= warn_limit, True)

print("\n=== ตัวตัดความยาว ===")
check("ค่าสั้นไม่ถูกแตะ", models.clamp("abc", 10), "abc")
check("  ค่ายาวถูกตัดพอดี", len(models.clamp("x" * 999, 100)), 100)
check("  None ยังเป็น None", models.clamp(None, 10), None)
check("  ตัวเลขแปลงเป็นข้อความ", models.clamp(12345, 3), "123")

print("\n=== caption ยาวสุดของ Telegram ต้องบันทึกได้ ไม่ทำให้สลิปหาย ===")
huge_caption = (
    "TRANS ID : 7090915\n"
    "FULL NAME : " + "ประกอบกิจเจริญรุ่งเรืองวัฒนา" * 12 + "\n"
    "AMOUNT THB : 3,500.00\n"
    + "หมายเหตุยาวมาก " * 60
)
huge_caption = huge_caption[:TELEGRAM_CAPTION_MAX]
check("caption ทดสอบยาวเท่าขีดจำกัดจริง", len(huge_caption), TELEGRAM_CAPTION_MAX)

with SessionLocal() as db:
    status, txn = process_incoming_slip(db, ["QR_LONG_1"], "-100111", "9001", huge_caption)
check("  รับเข้าระบบได้", status, "new")

with SessionLocal() as db:
    saved = db.query(Transaction).filter(Transaction.msg_id == "9001").first()
check("  บันทึกลงฐานข้อมูลแล้ว", saved is not None, True)
for column in ["raw_caption", "chat_fullname", "chat_trans_id", "caption_warning"]:
    limit = column_length(Transaction, column)
    value = getattr(saved, column) or ""
    ok = len(value) <= limit
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {column}: เก็บ {len(value)} ตัว / คอลัมน์รับ {limit}")
check("  ยอดเงินยังอ่านถูก", saved.chat_amount, 3500.0)

print("\n=== ด่านกันสลิปซ้ำ ต้องไม่ล้มเพราะ QR ยาว ===")
long_qr = "00020101021229370016A000000677010111" + "9" * 300
with SessionLocal() as db:
    process_incoming_slip(db, [long_qr], "-100111", "9002", "TRANS ID : 1\nAMOUNT THB : 100")

# เอาสลิปใบเดิมมามัดรวมกับใบใหม่แล้วส่งอีกรอบ — ชุดนี้ได้ batch_id คนละอันกับรอบแรก
# จึงเข้าเส้นทาง "QR ใบนี้เคยถูกใช้ไปแล้ว" ซึ่งเป็นจุดที่เคยเอา payload ไปต่อเป็นชื่อ action
with SessionLocal() as db:
    status, _ = process_incoming_slip(
        db, [long_qr, "QR_OTHER"], "-100111", "9003", "TRANS ID : 2\nAMOUNT THB : 100")
check("จับซ้ำได้", status, "duplicate")

with SessionLocal() as db:
    actions = [a.action for a in db.query(models.AuditLog).all()]
    action_limit = column_length(models.AuditLog, "action")
    longest = max((len(a) for a in actions), default=0)
check("  ชื่อ action ไม่มี QR payload ปนมา", "duplicate_qr_found" in actions, True)
check("  ไม่มี action ไหนยาวเกินคอลัมน์", longest <= action_limit, True)

with SessionLocal() as db:
    stored = db.query(UsedQR).filter(UsedQR.qr_ref == long_qr).first()
check("  เก็บ QR เต็มความยาว ไม่ตัด (ตัดแล้วจะจับซ้ำผิดใบ)",
      stored is not None and stored.qr_ref == long_qr, True)

print("\n=== การส่งซ้ำต้องตามดูได้จาก 'ใบแรก' ===")
# transactions เก็บสลิปใบละแถวโดยตั้งใจ (batch_id คำนวณจาก QR) การส่งซ้ำจึงไม่สร้างแถวใหม่
# ประวัติการส่งซ้ำต้องไปผูกกับใบแรกที่มีแถวอยู่จริง ไม่งั้นจะลอยไปหาไม่เจอ
with SessionLocal() as db:
    first = db.query(Transaction).filter(Transaction.msg_id == "9002").first()
    original_batch = first.batch_id
    history = [a.action for a in get_audit_trail(db, original_batch)]

check("มีแถวเดียวต่อสลิปหนึ่งใบ",
      SessionLocal().query(Transaction).filter(Transaction.chat_trans_id == "1").count(), 1)
check("  ประวัติการส่งซ้ำอยู่กับใบแรก", "duplicate_qr_found" in history, True)

with SessionLocal() as db:
    orphan = [
        a.qr_ref for a in db.query(models.AuditLog).all()
        if a.action.startswith("duplicate")
        and db.query(Transaction).filter(Transaction.batch_id == a.qr_ref).first() is None
    ]
check("  ไม่มีประวัติที่ผูกกับ batch ที่ไม่มีแถวจริง", orphan, [])

print("\n=== ยอดศูนย์หรือติดลบ ถือว่ายังไม่ได้แจ้งยอด ===")
for caption, label in [("Amount: 0", "ศูนย์"), ("Amount: 0.00", "ศูนย์ทศนิยม")]:
    data = extract_data_from_caption(caption)
    check(f"{label} -> ไม่นับเป็นยอด", data["amount"], None)
    check(f"  {label} ติดธงให้คนดู", data["amount_note"] is not None, True)

check("ยอดปกติยังใช้ได้", extract_data_from_caption("Amount: 50")["amount"], 50.0)
check("  ไม่ติดธง", extract_data_from_caption("Amount: 50")["amount_note"], None)

print("\n=== audit log ที่ยาวผิดปกติ ต้องไม่ทำให้ล้ม ===")
with SessionLocal() as db:
    add_audit_log(db, "b_long", "x" * 500, actor="y" * 500)
    trail = get_audit_trail(db, "b_long")
check("บันทึกได้ ไม่โยน error", len(trail), 1)
check("  ถูกตัดพอดีคอลัมน์", len(trail[0].action) <= column_length(models.AuditLog, "action"), True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
