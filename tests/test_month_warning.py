"""ทดสอบการเตือนก่อนขึ้นเดือนใหม่ 2 ชั่วโมง"""
import os
import sys
import types
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone, timedelta as dt_timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
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

from apscheduler.triggers.cron import CronTrigger  # noqa: E402
import scheduler as sched  # noqa: E402
import services.notifier as notifier  # noqa: E402

# เวลาที่ scheduler ใช้ = ปฏิทินธุรกิจ (UTC+8) ไม่ใช่นาฬิกาไทย
BKK = dt_timezone(dt_timedelta(hours=8), "UTC+8")
failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


# ---------- ของปลอม ----------
DRIVE_FILES = set()   # เก็บเป็น "โฟลเดอร์ปี/ชื่อไฟล์"
LOOKUPS = []
SENT = []


def fake_folder_lookup(name, parent_id=None):
    LOOKUPS.append(name)
    has_folder = any(path.startswith(name + "/") for path in DRIVE_FILES) or name in FOLDERS_ONLY
    return f"folder-{name}" if has_folder else None


def fake_lookup(name, parent_id=None):
    LOOKUPS.append(name)
    key = f"{str(parent_id).replace('folder-', '')}/{name}"
    return "id-" + key if key in DRIVE_FILES else None


FOLDERS_ONLY = set()   # โฟลเดอร์ที่มีแต่ยังไม่มีไฟล์ข้างใน


def fake_send(chat_id, text):
    SENT.append({"chat_id": chat_id, "text": text})
    return True


sched.drive_service.get_spreadsheet_id_by_name = fake_lookup
sched.drive_service.get_folder_id_by_name = fake_folder_lookup
sched.send_telegram_message = fake_send


def run_at(when, existing_files=(), existing_folders=()):
    DRIVE_FILES.clear()
    DRIVE_FILES.update(existing_files)
    FOLDERS_ONLY.clear()
    FOLDERS_ONLY.update(existing_folders)
    LOOKUPS.clear()
    SENT.clear()
    sched.get_business_now = lambda: when
    sched.warn_if_next_month_file_missing()


print("=== มีโฟลเดอร์ปีแล้ว แต่ยังไม่มีไฟล์เดือนหน้า ===")
run_at(datetime(2026, 8, 31, 22, 0, tzinfo=BKK), existing_folders={"Deposit-2026"})
check("เช็คครบ 2 ชั้น", LOOKUPS, ["Deposit-2026", "check_09-2026"])
check("  ส่งเข้าครบทั้ง 2 กลุ่ม", sorted(s["chat_id"] for s in SENT), ["-100111", "-100222"])
check("  ข้อความบอกชื่อไฟล์ที่ต้องสร้าง", "check_09-2026" in SENT[0]["text"], True)
check("  บอก path เต็ม", "Deposit-2026/check_09-2026" in SENT[0]["text"], True)
check("  บอกว่าสร้างในโฟลเดอร์ที่มีอยู่แล้ว", "inside the existing" in SENT[0]["text"], True)
check("  ไม่สั่งให้สร้างโฟลเดอร์ซ้ำ", "create a folder named" in SENT[0]["text"], False)
check("  เตือนเรื่องปี พ.ศ.", "2569" in SENT[0]["text"], True)
check("  บอกว่าไม่ต้องเตรียมอะไรในไฟล์", "creates the daily tabs itself" in SENT[0]["text"], True)
check("  กันสร้างซ้ำซ้อน", "creating a second one" in SENT[0]["text"], True)

print("\n=== ไม่มีทั้งโฟลเดอร์และไฟล์ -> ต้องบอกให้สร้างโฟลเดอร์ด้วย ===")
run_at(datetime(2026, 8, 31, 22, 0, tzinfo=BKK))
check("ค้นแค่โฟลเดอร์ ไม่ค้นไฟล์ต่อ", LOOKUPS, ["Deposit-2026"])
check("  สั่งให้สร้างโฟลเดอร์ก่อน", "create a folder named" in SENT[0]["text"], True)
check("  แล้วค่อยสร้างไฟล์ข้างใน", "inside it" in SENT[0]["text"], True)

print("\n=== มีไฟล์เดือนหน้าแล้ว -> ต้องเงียบ ===")
run_at(datetime(2026, 8, 31, 22, 0, tzinfo=BKK), existing_files={"Deposit-2026/check_09-2026"})
check("เช็คแล้ว", LOOKUPS, ["Deposit-2026", "check_09-2026"])
check("  ไม่ส่งอะไรเข้ากลุ่มเลย", len(SENT), 0)

print("\n=== ข้ามปี -> ต้องเช็คโฟลเดอร์ปีใหม่ ===")
run_at(datetime(2026, 12, 31, 22, 0, tzinfo=BKK), existing_folders={"Deposit-2026"})
check("เช็คโฟลเดอร์ของปีถัดไป", LOOKUPS, ["Deposit-2027"])
check("  บอกให้สร้างโฟลเดอร์ปีใหม่", "Deposit-2027" in SENT[0]["text"], True)
check("  แจ้งเตือนถูกไฟล์", "check_01-2027" in SENT[0]["text"], True)

print("\n=== กุมภาพันธ์ (28 วัน) ===")
run_at(datetime(2026, 2, 28, 22, 0, tzinfo=BKK), existing_folders={"Deposit-2026"})
check("เช็คไฟล์เดือนมีนา", LOOKUPS, ["Deposit-2026", "check_03-2026"])

print("\n=== Drive ล่ม -> ต้องไม่แจ้งเตือนมั่ว ===")
DRIVE_FILES.clear()
LOOKUPS.clear()
SENT.clear()


def broken_lookup(name, parent_id=None):
    raise RuntimeError("drive is down")


sched.drive_service.get_folder_id_by_name = broken_lookup
sched.get_business_now = lambda: datetime(2026, 8, 31, 22, 0, tzinfo=BKK)
sched.warn_if_next_month_file_missing()
check("ไม่โยน exception ออกมา", True, True)
check("  ไม่ส่งข้อความมั่ว", len(SENT), 0)
sched.drive_service.get_folder_id_by_name = fake_folder_lookup

print("\n=== CronTrigger ตัวจริง: day='last' ยิงวันสุดท้ายของทุกเดือน ===")
trigger = CronTrigger(day="last", hour=22, minute=0, timezone=BKK)
fire_times = []
cursor = datetime(2026, 1, 1, tzinfo=BKK)
for _ in range(14):
    nxt = trigger.get_next_fire_time(None, cursor)
    fire_times.append(nxt.strftime("%Y-%m-%d %H:%M"))
    cursor = nxt + timedelta(minutes=1)

check("ยิงเดือนละครั้ง 14 เดือนติด", len(fire_times), 14)
check("  ครึ่งปีแรก 2026", fire_times[:6], [
    "2026-01-31 22:00", "2026-02-28 22:00", "2026-03-31 22:00",
    "2026-04-30 22:00", "2026-05-31 22:00", "2026-06-30 22:00",
])
check("  ข้ามปีต่อเนื่อง", fire_times[11:14], [
    "2026-12-31 22:00", "2027-01-31 22:00", "2027-02-28 22:00",
])
check("  ทุกครั้งเป็น 22:00", all(t.endswith("22:00") for t in fire_times), True)
check("  ห่างจากสิ้นเดือน 2 ชม. เป๊ะ", all(
    (datetime.strptime(t, "%Y-%m-%d %H:%M").replace(tzinfo=BKK) + timedelta(hours=2)).day == 1
    for t in fire_times
), True)

print("\n=== ปีอธิกสุรทิน (2028) ===")
leap = CronTrigger(day="last", hour=22, minute=0, timezone=BKK)
feb = leap.get_next_fire_time(None, datetime(2028, 2, 1, tzinfo=BKK))
check("29 ก.พ. 2028", feb.strftime("%Y-%m-%d %H:%M"), "2028-02-29 22:00")

print("\n=== ตัวส่งข้อความ ===")
calls = []


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def fake_post(url, json=None, timeout=None):
    calls.append({"url": url, "json": json, "timeout": timeout})
    return FakeResponse(200)


notifier.requests = types.SimpleNamespace(post=fake_post)
ok = notifier.send_telegram_message("-100111", "hello")
check("ส่งสำเร็จ", ok, True)
check("  ยิงไปที่ Bot API ถูก endpoint", calls[0]["url"].endswith("/bottest-token/sendMessage"), True)
check("  ส่ง chat_id กับ text ครบ", calls[0]["json"], {"chat_id": "-100111", "text": "hello"})
check("  มี timeout กันค้าง", calls[0]["timeout"], 15)

notifier.requests = types.SimpleNamespace(post=lambda *a, **k: FakeResponse(403, "forbidden"))
check("Telegram ปฏิเสธ -> คืน False", notifier.send_telegram_message("-100111", "hi"), False)


def raising_post(*a, **k):
    raise RuntimeError("network down")


notifier.requests = types.SimpleNamespace(post=raising_post)
check("เน็ตล่ม -> คืน False ไม่โยน exception", notifier.send_telegram_message("-100111", "hi"), False)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
