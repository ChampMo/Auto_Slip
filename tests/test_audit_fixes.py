from datetime import datetime as _dt, timedelta as _td, timezone as _tz
"""ทดสอบสิ่งที่แก้จากการตรวจจุดบอด (ข้อ 1,2,5,8,9,15,19)"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "audit.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
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

import bot.handlers as h  # noqa: E402
import bot.recovery as recovery  # noqa: E402
from database.crud import add_audit_log, find_interrupted_batches  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction, AuditLog  # noqa: E402
from services.easyslip import (  # noqa: E402
    describe_receiver_account,
    get_receiver_account_value,
    match_company_account,
)
from services.gsheets import sheets_service  # noqa: E402
from core.matcher import REJECTED_STATUSES  # noqa: E402

init_db()

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

    async def send_message(self, chat_id, text, reply_to_message_id=None, reply_markup=None,
                           message_thread_id=None):
        self.messages.append({"chat_id": chat_id, "text": text, "keyboard": reply_markup})
        return SimpleNamespace(message_id=9000 + len(self.messages))


def new_slip(batch_id, status="pending", chat_id="-100111", **kw):
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.query(AuditLog).filter(AuditLog.qr_ref == batch_id).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status=status,
            chat_id=chat_id, msg_id="1", chat_amount=400.0, chat_trans_id="0001", **kw,
        ))
        db.commit()


def status_of(batch_id):
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        return txn.status if txn else None


print("=== ข้อ 15: ใช้เลขบัญชีจับคู่ ไม่ใช่รหัสธนาคาร ===")
# bank.code คือรหัสธนาคาร คนละความหมายกับเลขบัญชี ห้ามเอามาจับคู่
recv = {"bank": {"code": "1234", "account": "XXX-X-XX298-4"}}
check("ใช้เลขบัญชีจับคู่ ไม่สนใจ code",
      match_company_account(get_receiver_account_value(recv))[0], "KKP-LS")
check("  ดึงเลขบัญชีดิบออกมาได้", get_receiver_account_value(recv), "XXX-X-XX298-4")
check("  ไม่มีเลขบัญชี -> ไม่จับคู่ให้",
      match_company_account(get_receiver_account_value({"bank": {"code": "1234"}}))[1], "no_account")
check("  code เอาไว้แสดงผลเท่านั้น",
      describe_receiver_account({"bank": {"code": "1234"}}), "1234")
check("  ไม่มีอะไรเลย", describe_receiver_account({}), "")

print("\n=== ข้อ 5: รับสลิปที่ส่งมาเป็นไฟล์ ===")
photo_msg = SimpleNamespace(photo=[MagicMock()], document=None)
doc_msg = SimpleNamespace(photo=None, document=SimpleNamespace(mime_type="image/jpeg"))
pdf_msg = SimpleNamespace(photo=None, document=SimpleNamespace(mime_type="application/pdf"))
none_msg = SimpleNamespace(photo=None, document=None)
check("รูปปกติ", h.pick_image_source(photo_msg) is photo_msg.photo[-1], True)
check("  ส่งเป็นไฟล์รูป", h.pick_image_source(doc_msg) is doc_msg.document, True)
check("  ไฟล์ PDF -> ไม่รับ (pyzbar อ่านไม่ได้)", h.pick_image_source(pdf_msg), None)
check("  ไม่มีอะไรเลย", h.pick_image_source(none_msg), None)

print("\n=== ข้อ 8: กู้สลิปที่ค้างเพราะบอทดับกลางบันทึก ===")
check("interrupted อยู่ในกลุ่มที่ส่งซ้ำได้", "interrupted" in REJECTED_STATUSES, True)

new_slip("stuck1", status="Receive")
new_slip("done1", status="Receive")
new_slip("normal1")
with SessionLocal() as db:
    add_audit_log(db, "stuck1", "saving_started")          # ค้างตรงนี้ ไม่มีผลตามมา
    add_audit_log(db, "done1", "saving_started")
    add_audit_log(db, "done1", "sheet_saved")              # จบเรียบร้อย
    check("หาเจอเฉพาะตัวที่ค้าง", find_interrupted_batches(db), ["stuck1"])

sent = []
recovery.send_telegram_message = lambda chat_id, text: sent.append({"chat_id": chat_id, "text": text}) or True
count = recovery.recover_interrupted_slips()
check("กู้ได้ 1 รายการ", count, 1)
check("  เปลี่ยนเป็น interrupted", status_of("stuck1"), "interrupted")
check("  ตัวที่บันทึกจบแล้วไม่ถูกแตะ", status_of("done1"), "Receive")
check("  แจ้งเข้ากลุ่มต้นทางกลุ่มเดียว", [s["chat_id"] for s in sent], ["-100111"])
check("  ข้อความบอกให้ส่งใหม่", "send these slips again" in sent[0]["text"], True)
check("  ข้อความบอก ID", "0001" in sent[0]["text"], True)

sent.clear()
check("เรียกซ้ำต้องไม่กู้อะไรอีก", recovery.recover_interrupted_slips(), 0)
check("  ไม่ส่งข้อความซ้ำ", len(sent), 0)


# ---------- ข้อ 2 + 19: verify ไม่ถือ session และไม่ค้าง event loop ----------
async def flow_tests():
    print("\n=== ข้อ 2/19: ตรวจสลิปต้องไม่แช่แข็งบอท และไม่ถือ DB session ===")
    import time

    ticks = []

    async def heartbeat():
        for _ in range(30):
            await asyncio.sleep(0.01)
            ticks.append(1)

    def slow_verify(payload):
        time.sleep(0.12)          # จำลอง EasySlip ตอบช้า (โค้ด blocking)
        return {
            "success": True, "amount": 400.0, "payload_amount": 400.0,
            "sender": "นาย ดุลยฤทธิ์ ส", "receiver": "KKP-LS",
            "receiver_bank_code": "2984", "receiver_bank_matches": True,
            "transfer_at": _dt(2026, 8, 9, 0, 18, tzinfo=_tz(_td(hours=7))),
            "raw_data": {"x": 1},
        }

    h.verify_slip = slow_verify
    h.append_to_sheet = lambda entry: (True, "")

    bot = FakeBot()
    beat = asyncio.create_task(heartbeat())
    started = time.monotonic()
    await h.process_slip_group(
        bot, "-100111", 900,
        "TRANS ID : 0000009\nFULL NAME : ดุลยฤทธิ์\nAMOUNT THB : 400.00",
        ["QR_SLOW"], 1,
    )
    elapsed = time.monotonic() - started
    beat.cancel()

    check("รับอัตโนมัติสำเร็จ", "Received automatically" in bot.messages[-1]["text"], True)
    check("  รอ API จริงตามเวลา", elapsed >= 0.12, True)
    check("  event loop ยังเดินระหว่างรอ API", len(ticks) >= 5, True)

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.msg_id == "900").first()
        check("  บันทึกผลตรวจลง DB", txn.api_total_amount, 400.0)
        check("  เก็บ raw_data ของ QR", db.query(AuditLog).filter(
            AuditLog.qr_ref == txn.batch_id, AuditLog.action == "sheet_saved").count(), 1)

    print("\n=== ข้อ 9: flush อัลบั้มที่ค้างตอนปิดบอท ===")
    processed = []

    async def fake_group(bot_, chat_id, msg_id, caption, qr_list, photo_count,
                         photo_hashes=None, allow_without_qr=False, **kw):
        processed.append({"msg_id": msg_id, "qr": list(qr_list), "photos": photo_count})

    real = h.process_slip_group
    h.process_slip_group = fake_group
    h.MEDIA_GROUP_WAIT_SECONDS = 60      # ตั้งให้ยาว จะได้ค้างแน่ๆ

    await h.collect_media_group_photo("G_SHUT", MagicMock(), "-100111", 10, "Amount : THB 400", ["QR_A"])
    await h.collect_media_group_photo("G_SHUT", MagicMock(), "-100111", 11, "", ["QR_B"])
    check("ยังไม่ถูกประมวลผล (รออยู่)", len(processed), 0)
    check("  มีอัลบั้มค้างใน buffer", len(h._media_groups), 1)

    await h.flush_pending_media_groups(MagicMock())
    check("flush แล้วประมวลผลทันที", len(processed), 1)
    check("  ได้ QR ครบทั้งอัลบั้ม", processed[0]["qr"], ["QR_A", "QR_B"])
    check("  buffer ถูกเคลียร์", len(h._media_groups), 0)

    await h.flush_pending_media_groups(MagicMock())
    check("  flush ซ้ำต้องไม่ทำอะไร", len(processed), 1)

    h.process_slip_group = real


asyncio.run(flow_tests())


print("\n=== ข้อ 1/11: ตารางสรุปเขียนแค่ตอนสร้างแท็บ ===")
check("เลิกใช้ update_daily_summary แล้ว", hasattr(sheets_service, "update_daily_summary"), False)
check("  มีการแต่งเฉพาะแถวใหม่แทน", hasattr(sheets_service, "_apply_row_style"), True)

import inspect  # noqa: E402
append_src = inspect.getsource(sheets_service.append_to_sheet)
check("  append ไม่เรียกจัดสีทั้งตาราง", "_apply_main_table_style" in append_src, False)
check("  append ไม่เขียนตารางสรุปใหม่", "_write_summary_tables" in append_src, False)
check("  append แต่งเฉพาะแถวใหม่", "_apply_row_style" in append_src, True)

summary_src = inspect.getsource(sheets_service._write_summary_tables)
check("  ไม่มีแถวลูกค้าที่ทำให้คอลัมน์เลื่อน", "customers" in summary_src, False)


class CaptureWorksheet:
    """จับค่าที่ถูกเขียนลงโซนสรุป เพื่อตรวจว่าทุกแถวกว้างเท่ากัน"""

    def __init__(self):
        self.written = None
        self.range = None
        self.spreadsheet = MagicMock()
        self.id = 1
        self._properties = {"sheetId": 1}

    def update(self, values, range_name=None, **kwargs):
        self.written = values
        self.range = range_name


ws = CaptureWorksheet()
sheets_service._write_summary_tables(ws)
widths = sorted({len(row) for row in ws.written})
check("ทุกแถวในโซนสรุปกว้างเท่ากัน", widths, [19])
check("  ช่วงที่เขียนคือ AK ถึง BC", ws.range.startswith("AK1:BC"), True)
check("  จำนวนแถวตรงกับข้อมูลที่ส่งไป", ws.range, f"AK1:BC{len(ws.written)}")

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
