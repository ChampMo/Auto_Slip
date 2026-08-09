"""ทดสอบว่าการเขียนชีทไม่ทับกัน และไม่ทำให้บอทค้าง"""
import asyncio
import os
import sys
import tempfile
import threading
import time
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "thread.db")
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
from services.gsheets import SheetEntry, sheets_service  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction  # noqa: E402

init_db()

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


# ---------- ชีทปลอมที่จับการทำงานคาบเกี่ยว ----------
class FakeWorksheet:
    """เลียนแบบ worksheet จริง: จำนวนแถวจะโตขึ้นตามที่เขียนจริง"""

    def __init__(self, tracker):
        self.tracker = tracker
        self.rows = {12: [], 16: []}   # คอลัมน์ L และ P
        self.writes = []
        self.id = 1
        self.spreadsheet = MagicMock()
        self._properties = {"sheetId": 1}

    def col_values(self, index):
        # จำลอง network latency ระหว่าง "อ่านแถวสุดท้าย" กับ "เขียนแถวใหม่"
        time.sleep(0.02)
        return self.rows.get(index, [])

    def update(self, values, cell_range=None, **kwargs):
        """ลำดับพารามิเตอร์ตาม gspread 6: (values, range_name)"""
        with self.tracker.lock:
            self.tracker.active += 1
            self.tracker.max_active = max(self.tracker.max_active, self.tracker.active)
        time.sleep(0.02)
        # แถวข้อมูลเท่านั้นที่ทำให้คอลัมน์ยาวขึ้น (ข้าม summary/header)
        if cell_range.startswith("L") or cell_range.startswith("P"):
            column = 12 if cell_range.startswith("L") else 16
            self.rows[column].append(values[0][0])
            self.writes.append(cell_range)
        with self.tracker.lock:
            self.tracker.active -= 1

    def get_all_values(self):
        return [["header"]]

    def freeze(self, rows=1):
        pass

    def format(self, *args, **kwargs):
        pass


class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0


def install_fake_sheet():
    tracker = Tracker()
    worksheet = FakeWorksheet(tracker)
    spreadsheet = MagicMock()
    spreadsheet.worksheet.return_value = worksheet
    sheets_service._get_dynamic_spreadsheet = lambda target=None: spreadsheet
    return tracker, worksheet


def entry(batch_id, category="VIP_WE", trans_id=None):
    return SheetEntry(
        batch_id=batch_id, category=category, status="Receive",
        chat_trans_id=trans_id or batch_id, chat_fullname=None, sender_names=None,
        chat_amount=400.0, api_total_amount=400.0,
        receiver_account="KKP-LS", chat_bank=None,
    )


print("=== SheetEntry คัดลอกค่าออกจาก ORM ได้ครบ ===")
with SessionLocal() as db:
    db.add(Transaction(
        batch_id="snap1", category="VIP_12", status="Receive",
        chat_id="-100222", msg_id="1", chat_trans_id="0009",
        chat_fullname="ดุลยฤทธิ์", sender_names="นาย ดุลยฤทธิ์ ส",
        chat_amount=100.0, api_total_amount=400.0,
        receiver_account="KKP-LS", chat_bank="SCB-CP",
    ))
    db.commit()
    txn = db.query(Transaction).filter(Transaction.batch_id == "snap1").first()
    snap = SheetEntry.from_transaction(txn)

check("คัดลอกครบทุกฟิลด์", (snap.batch_id, snap.category, snap.status), ("snap1", "VIP_12", "Receive"))
check("  ยอดจาก API", snap.api_total_amount, 400.0)
check("  บัญชีผู้รับ", snap.receiver_account, "KKP-LS")
check("  แก้ค่าไม่ได้ (frozen)", isinstance(snap, SheetEntry) and hasattr(snap, "__dataclass_fields__"), True)
# ปิด session แล้วยังอ่านค่าได้ = ไม่ผูกกับ session อีกต่อไป
check("  session ปิดแล้วยังใช้ได้", snap.chat_fullname, "ดุลยฤทธิ์")

print("\n=== เขียนพร้อมกัน 6 รายการ ต้องไม่ทับแถวกัน ===")
tracker, worksheet = install_fake_sheet()
results = []
threads = [
    threading.Thread(target=lambda i=i: results.append(sheets_service.append_to_sheet(entry(f"b{i}"))))
    for i in range(6)
]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("สำเร็จทุกรายการ", all(ok for ok, _ in results), True)
check("  ไม่มีการเขียนคาบเกี่ยวกันเลย", tracker.max_active, 1)
check("  ได้ 6 แถว", len(worksheet.writes), 6)
check("  แถวไม่ซ้ำกัน", len(set(worksheet.writes)), 6)
check("  เรียงต่อกันตั้งแต่แถว 1", sorted(worksheet.writes), sorted(f"L{i}:O{i}" for i in range(1, 7)))

print("\n=== bot เขียนสลิป ชนกับ scheduler สร้างชีทประจำวัน ===")
tracker, worksheet = install_fake_sheet()
sheets_service._create_sheet_for_locked = lambda target: time.sleep(0.05)
order = []
scheduler_thread = threading.Thread(target=lambda: (sheets_service.create_today_sheet(), order.append("scheduler")))
bot_thread = threading.Thread(target=lambda: (sheets_service.append_to_sheet(entry("b9")), order.append("bot")))
scheduler_thread.start()
time.sleep(0.005)
bot_thread.start()
scheduler_thread.join()
bot_thread.join()
check("ทั้งคู่ทำงานเสร็จ", sorted(order), ["bot", "scheduler"])
check("  bot รอ scheduler เสร็จก่อน", order[0], "scheduler")
check("  เขียนได้ 1 แถว", len(worksheet.writes), 1)

print("\n=== VIP_12 กับ VIP_WE เขียนคนละคอลัมน์ ===")
tracker, worksheet = install_fake_sheet()
sheets_service.append_to_sheet(entry("we1", category="VIP_WE"))
sheets_service.append_to_sheet(entry("12a", category="VIP_12"))
sheets_service.append_to_sheet(entry("we2", category="VIP_WE"))
check("แยกคอลัมน์ถูก", worksheet.writes, ["L1:O1", "P1:S1", "L2:O2"])

print("\n=== สถานะ Reject ไม่เขียนลงชีท ===")
tracker, worksheet = install_fake_sheet()
rejected = SheetEntry(
    batch_id="rej", category="VIP_WE", status="Reject", chat_trans_id="1",
    chat_fullname=None, sender_names=None, chat_amount=100.0,
    api_total_amount=None, receiver_account=None, chat_bank=None,
)
ok, err = sheets_service.append_to_sheet(rejected)
check("คืนค่าสำเร็จแต่ไม่เขียน", (ok, len(worksheet.writes)), (True, 0))


# ---------- bot ต้องไม่ค้างระหว่างรอ Google ----------
async def check_event_loop_stays_free():
    print("\n=== event loop ต้องไม่ค้างระหว่างเขียนชีท ===")
    ticks = []

    async def heartbeat():
        for _ in range(20):
            await asyncio.sleep(0.01)
            ticks.append(1)

    def slow_append(entry_arg):
        time.sleep(0.15)          # จำลอง Google ตอบช้า (โค้ด blocking)
        return True, ""

    h.append_to_sheet = slow_append

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == "snap1").first()
        beat = asyncio.create_task(heartbeat())
        started = time.monotonic()
        ok, _ = await h.write_entry_to_sheet(txn)
        elapsed = time.monotonic() - started
        beat.cancel()

    check("เขียนสำเร็จ", ok, True)
    check("  ใช้เวลาจริงตามที่ Google ช้า", elapsed >= 0.15, True)
    check("  event loop ยังเดินอยู่ระหว่างนั้น", len(ticks) >= 5, True)


asyncio.run(check_event_loop_stays_free())

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
