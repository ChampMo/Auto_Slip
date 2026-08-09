"""ทดสอบการสร้างแท็บรายวัน โดยเฉพาะคืนวันสิ้นเดือนที่ต้องข้ามไปไฟล์เดือนถัดไป"""
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

for module_name in [
    "telegram", "telegram.ext", "telegram.error",
    "gspread", "gspread.exceptions",
    "google", "google.oauth2", "google.oauth2.service_account",
    "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
    "requests", "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image",
    "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
    "apscheduler.triggers", "apscheduler.triggers.cron",
]:
    sys.modules.setdefault(module_name, MagicMock(name=module_name))

gspread_exceptions = types.ModuleType("gspread.exceptions")
gspread_exceptions.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
gspread_exceptions.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = gspread_exceptions
sys.modules["gspread"].exceptions = gspread_exceptions

import services.gdrive as gdrive  # noqa: E402
from services.gsheets import SheetEntry, sheets_service  # noqa: E402
import scheduler as sched  # noqa: E402

# เวลาที่ scheduler ใช้ = ปฏิทินธุรกิจ (UTC+8) ไม่ใช่นาฬิกาไทย
BANGKOK = dt_timezone(dt_timedelta(hours=8), "UTC+8")
failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


# ---------- Drive/Sheets ปลอม ----------
class FakeWorksheet:
    def __init__(self, title):
        self.title = title

    def col_values(self, index):
        return []

    def update(self, *a, **k):
        pass

    def get_all_values(self):
        return [["header"]]

    def freeze(self, rows=1):
        pass

    def format(self, *a, **k):
        pass

    id = 1
    _properties = {"sheetId": 1}
    spreadsheet = MagicMock()


class FakeSpreadsheet:
    def __init__(self, file_name):
        self.file_name = file_name
        self.tabs = {}

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread_exceptions.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title)
        return self.tabs[title]

    def del_worksheet(self, ws):
        self.tabs.pop(getattr(ws, "title", None), None)


DRIVE = {}          # "โฟลเดอร์ปี/ชื่อไฟล์" -> FakeSpreadsheet
FOLDERS = set()     # โฟลเดอร์รายปีที่มีอยู่จริง
LOOKUPS = []        # สิ่งที่ถูกค้นหา ตามลำดับ


def fake_folder_lookup(name, parent_id=None):
    LOOKUPS.append(name)
    return f"folder-{name}" if name in FOLDERS else None


def fake_file_lookup(name, parent_id=None):
    LOOKUPS.append(name)
    key = f"{str(parent_id).replace('folder-', '')}/{name}"
    return key if key in DRIVE else None


gdrive.drive_service.get_folder_id_by_name = fake_folder_lookup
gdrive.drive_service.get_spreadsheet_id_by_name = fake_file_lookup
sheets_service.client = MagicMock()
sheets_service.client.open_by_key = lambda key: DRIVE[key]


def reset_drive(*paths):
    """paths เช่น "Deposit-2026/check_08-2026" """
    DRIVE.clear()
    FOLDERS.clear()
    LOOKUPS.clear()
    for path in paths:
        DRIVE[path] = FakeSpreadsheet(path)
        FOLDERS.add(path.split("/")[0])


def tabs_of(path):
    return sorted(DRIVE[path].tabs)


print("=== สร้างแท็บของวันนี้ตามปกติ ===")
reset_drive("Deposit-2026/check_08-2026")
sheets_service.create_sheet_for(datetime(2026, 8, 4, 10, 0, tzinfo=BANGKOK))
check("ได้แท็บของวันที่ระบุ", tabs_of("Deposit-2026/check_08-2026"), ["04-08-2026"])
check("  ค้นโฟลเดอร์ปีก่อน แล้วค่อยค้นไฟล์", LOOKUPS, ["Deposit-2026", "check_08-2026"])

print("\n=== เรียกซ้ำ ต้องไม่สร้างแท็บเบิ้ล ===")
sheets_service.create_sheet_for(datetime(2026, 8, 4, 23, 59, tzinfo=BANGKOK))
check("ยังมีแท็บเดียว", tabs_of("Deposit-2026/check_08-2026"), ["04-08-2026"])

print("\n=== 23:59 คืนวันธรรมดา -> สร้างแท็บพรุ่งนี้ในไฟล์เดิม ===")
reset_drive("Deposit-2026/check_08-2026")
sched.get_business_now = lambda: datetime(2026, 8, 4, 23, 59, tzinfo=BANGKOK)
sched.create_next_day_sheet()
check("ได้แท็บของวันพรุ่งนี้", tabs_of("Deposit-2026/check_08-2026"), ["05-08-2026"])

print("\n=== 23:59 คืนวันสิ้นเดือน -> ต้องข้ามไปไฟล์เดือนถัดไป ===")
reset_drive("Deposit-2026/check_08-2026", "Deposit-2026/check_09-2026")
sched.get_business_now = lambda: datetime(2026, 8, 31, 23, 59, tzinfo=BANGKOK)
sched.create_next_day_sheet()
check("แท็บ 01-09 อยู่ในไฟล์เดือนกันยา", tabs_of("Deposit-2026/check_09-2026"), ["01-09-2026"])
check("  ไม่ไปโผล่ในไฟล์เดือนสิงหา", tabs_of("Deposit-2026/check_08-2026"), [])
check("  ค้นหาในโฟลเดอร์ปีเดิม", LOOKUPS, ["Deposit-2026", "check_09-2026"])

print("\n=== 23:59 คืนวันสิ้นปี ===")
reset_drive("Deposit-2026/check_12-2026", "Deposit-2027/check_01-2027")
sched.get_business_now = lambda: datetime(2026, 12, 31, 23, 59, tzinfo=BANGKOK)
sched.create_next_day_sheet()
check("ข้ามปีถูกต้อง", tabs_of("Deposit-2027/check_01-2027"), ["01-01-2027"])

print("\n=== คืนสิ้นเดือนแต่ยังไม่มีไฟล์เดือนหน้า ===")
reset_drive("Deposit-2026/check_08-2026")
sched.get_business_now = lambda: datetime(2026, 8, 31, 23, 59, tzinfo=BANGKOK)
sched.create_next_day_sheet()   # ต้องไม่โยน exception ออกมาให้ scheduler ตาย
check("ไม่ทำให้ scheduler ล้ม", True, True)
check("  ไม่ไปสร้างมั่วในไฟล์เดือนสิงหา", tabs_of("Deposit-2026/check_08-2026"), [])

print("\n=== เขียนสลิปตอนที่ยังไม่มีแท็บ -> สร้างให้เองด้วยวันที่ของสลิป ===")
reset_drive("Deposit-2026/check_08-2026")
import services.gsheets as gs  # noqa: E402
gs.get_business_now = lambda: datetime(2026, 8, 4, 0, 30, tzinfo=BANGKOK)
ok, err = sheets_service.append_to_sheet(SheetEntry(
    batch_id="b1", category="VIP_WE", status="Receive", chat_trans_id="0001",
    chat_fullname=None, sender_names=None, chat_amount=400.0,
    api_total_amount=400.0, receiver_account="KKP-LS", chat_bank=None,
))
check("บันทึกสำเร็จ", ok, True)
check("  สร้างแท็บของวันใหม่ให้เอง", tabs_of("Deposit-2026/check_08-2026"), ["04-08-2026"])

def try_write():
    return sheets_service.append_to_sheet(SheetEntry(
        batch_id="b2", category="VIP_WE", status="Receive", chat_trans_id="0002",
        chat_fullname=None, sender_names=None, chat_amount=400.0,
        api_total_amount=400.0, receiver_account="KKP-LS", chat_bank=None,
    ))


print("\n=== ไม่มีโฟลเดอร์ปี -> บอกว่าโฟลเดอร์ไหนหาย ===")
reset_drive()
ok, err = try_write()
check("คืนค่าล้มเหลว", ok, False)
check("  ข้อความชี้ที่โฟลเดอร์", "Deposit-2026" in err and "โฟลเดอร์" in err, True)
check("  ค้นแค่โฟลเดอร์ ไม่ค้นไฟล์ต่อ", LOOKUPS, ["Deposit-2026"])

print("\n=== มีโฟลเดอร์ปีแล้ว แต่ไม่มีไฟล์เดือนนี้ -> บอกว่าไฟล์ไหนหาย ===")
reset_drive("Deposit-2026/check_01-2026")   # มีโฟลเดอร์ แต่เป็นไฟล์เดือนอื่น
LOOKUPS.clear()
ok, err = try_write()
check("คืนค่าล้มเหลว", ok, False)
check("  ข้อความชี้ที่ไฟล์", "check_08-2026" in err, True)
check("  ค้นครบ 2 ชั้น", LOOKUPS, ["Deposit-2026", "check_08-2026"])

print("\n=== ไม่ cache ID ข้ามการเขียน ===")
reset_drive("Deposit-2026/check_08-2026")
gs.get_business_now = lambda: datetime(2026, 8, 4, 10, 0, tzinfo=BANGKOK)
try_write()
LOOKUPS.clear()
try_write()
check("เขียนครั้งที่ 2 ก็ยังค้น Drive ใหม่", LOOKUPS, ["Deposit-2026", "check_08-2026"])

print("\n=== สลิปใบแรกของวัน (ต้องสร้างแท็บ) ต้องไม่ค้น Drive ซ้ำสองรอบ ===")
reset_drive("Deposit-2026/check_08-2026")
gs.get_business_now = lambda: datetime(2026, 8, 7, 0, 30, tzinfo=BANGKOK)
try_write()
check("ค้นแค่รอบเดียว (2 ชั้น)", LOOKUPS, ["Deposit-2026", "check_08-2026"])
check("  สร้างแท็บให้แล้ว", tabs_of("Deposit-2026/check_08-2026"), ["07-08-2026"])

print("\n=== ลบไฟล์แล้วสร้างใหม่ชื่อเดิมกลางเดือน ===")
reset_drive("Deposit-2026/check_08-2026")
gs.get_business_now = lambda: datetime(2026, 8, 4, 10, 0, tzinfo=BANGKOK)
try_write()
old_file = DRIVE["Deposit-2026/check_08-2026"]
DRIVE["Deposit-2026/check_08-2026"] = FakeSpreadsheet("ไฟล์ใหม่")   # คนลบทิ้งแล้วสร้างใหม่
try_write()
check("เขียนลงไฟล์ใหม่", len(DRIVE["Deposit-2026/check_08-2026"].tabs), 1)
check("  ไม่ไปเขียนทับไฟล์เก่าที่ถูกลบ", len(old_file.tabs), 1)   # ของเดิม 1 แท็บ ไม่เพิ่ม

print("\n=== ไฟล์ถูกลบกลางเดือน (ยังไม่สร้างใหม่) ===")
reset_drive("Deposit-2026/check_08-2026")
try_write()
DRIVE.clear()          # ไฟล์หายไปจาก Drive (อยู่ในถังขยะ)
FOLDERS.add("Deposit-2026")
ok, err = try_write()
check("ต้องแจ้ง error ไม่ใช่เขียนเงียบๆ", ok, False)
check("  บอกว่าไฟล์ไหนหาย", "check_08-2026" in err, True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
