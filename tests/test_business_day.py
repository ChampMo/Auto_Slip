"""ทดสอบวันทางธุรกิจ — วันเปลี่ยนตอน 5 ทุ่มตามเวลาไทย ไม่ใช่เที่ยงคืน"""
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
for m in ["requests", "gspread", "gspread.exceptions", "google", "google.oauth2",
          "google.oauth2.service_account", "googleapiclient", "googleapiclient.discovery",
          "googleapiclient.errors", "telegram", "telegram.ext", "telegram.error",
          "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image", "apscheduler",
          "apscheduler.schedulers", "apscheduler.schedulers.background",
          "apscheduler.triggers", "apscheduler.triggers.cron"]:
    sys.modules.setdefault(m, MagicMock(name=m))
ge = types.ModuleType("gspread.exceptions")
ge.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
ge.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = ge

from zoneinfo import ZoneInfo  # noqa: E402
from services.gsheets import (  # noqa: E402
    BANGKOK_TZ,
    BUSINESS_DAY_STARTS_AT,
    BUSINESS_TZ,
    get_month_file_name,
    get_sheet_name_for_datetime,
    get_year_folder_name,
    to_bangkok_clock,
)
from bot.commands import business_day_range_utc, to_bangkok  # noqa: E402

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def bkk(y, m, d, hh, mm=0):
    """เวลาตามนาฬิกาไทย"""
    return datetime(y, m, d, hh, mm, tzinfo=ZoneInfo("Asia/Bangkok"))


print("=== ค่าคงที่ ===")
check("วันธุรกิจเริ่ม 23:00", BUSINESS_DAY_STARTS_AT, "23:00")
check("  offset เป็น +8 ชม.", BUSINESS_TZ.utcoffset(None), timedelta(hours=8))
check("  เร็วกว่าเวลาไทย 1 ชม.",
      BUSINESS_TZ.utcoffset(None) - bkk(2026, 8, 6, 12).utcoffset(), timedelta(hours=1))

print("\n=== แท็บรายวัน: วันเปลี่ยนตอน 5 ทุ่ม ===")
check("22:59 ไทย = ยังเป็นวันที่ 6",
      get_sheet_name_for_datetime(bkk(2026, 8, 6, 22, 59)), "06-08-2026")
check("  23:00 ไทย = ขึ้นวันที่ 7 แล้ว",
      get_sheet_name_for_datetime(bkk(2026, 8, 6, 23, 0)), "07-08-2026")
check("  23:30 ไทย = วันที่ 7",
      get_sheet_name_for_datetime(bkk(2026, 8, 6, 23, 30)), "07-08-2026")
check("  เที่ยงคืน = ยังเป็นวันที่ 7 เหมือนเดิม",
      get_sheet_name_for_datetime(bkk(2026, 8, 7, 0, 0)), "07-08-2026")
check("  บ่ายโมง = วันที่ 7",
      get_sheet_name_for_datetime(bkk(2026, 8, 7, 13, 0)), "07-08-2026")

print("\n=== ข้ามเดือน: สลิป 5 ทุ่มวันสิ้นเดือนไปลงไฟล์เดือนใหม่ ===")
check("31 ส.ค. 22:59 -> ไฟล์เดือน 8",
      get_month_file_name(bkk(2026, 8, 31, 22, 59)), "check_08-2026")
check("  31 ส.ค. 23:00 -> ไฟล์เดือน 9",
      get_month_file_name(bkk(2026, 8, 31, 23, 0)), "check_09-2026")
check("  31 ส.ค. 23:00 -> แท็บ 01-09",
      get_sheet_name_for_datetime(bkk(2026, 8, 31, 23, 0)), "01-09-2026")

print("\n=== ข้ามปี ===")
check("31 ธ.ค. 22:59 -> ปี 2026",
      get_year_folder_name(bkk(2026, 12, 31, 22, 59)), "Deposit-2026")
check("  31 ธ.ค. 23:00 -> ปี 2027",
      get_year_folder_name(bkk(2026, 12, 31, 23, 0)), "Deposit-2027")
check("  31 ธ.ค. 23:00 -> ไฟล์เดือน 1 ปี 2027",
      get_month_file_name(bkk(2026, 12, 31, 23, 0)), "check_01-2027")

print("\n=== /today นับช่วงเดียวกับแท็บ ===")
start, end = business_day_range_utc(bkk(2026, 8, 7, 10, 0))
check("ช่วงยาว 24 ชั่วโมง", end - start, timedelta(days=1))


def in_range(moment_bkk):
    naive_utc = moment_bkk.astimezone(timezone.utc).replace(tzinfo=None)
    return start <= naive_utc < end


check("  สลิป 23:00 ของเมื่อวาน นับเป็นวันนี้", in_range(bkk(2026, 8, 6, 23, 0)), True)
check("  สลิป 22:59 ของเมื่อวาน ไม่นับ", in_range(bkk(2026, 8, 6, 22, 59)), False)
check("  สลิปเที่ยงคืนวันนี้ นับ", in_range(bkk(2026, 8, 7, 0, 0)), True)
check("  สลิปเที่ยงวันนี้ นับ", in_range(bkk(2026, 8, 7, 12, 0)), True)
check("  สลิป 22:59 วันนี้ นับ", in_range(bkk(2026, 8, 7, 22, 59)), True)
check("  สลิป 23:00 วันนี้ ไม่นับแล้ว (เป็นของพรุ่งนี้)", in_range(bkk(2026, 8, 7, 23, 0)), False)

print("\n=== ช่วงของ /today ต้องตรงกับชื่อแท็บเป๊ะ ===")
# ทุกนาทีในช่วงที่ /today นับ ต้องตกลงแท็บชื่อเดียวกันทั้งหมด
moment = start
names = set()
while moment < end:
    aware = moment.replace(tzinfo=timezone.utc)
    names.add(get_sheet_name_for_datetime(aware))
    moment += timedelta(minutes=37)
check("ทุกเวลาในช่วงตกแท็บเดียวกัน", len(names), 1)
check("  ชื่อแท็บถูกต้อง", names.pop(), "07-08-2026")

print("\n=== แสดงเวลาให้คนอ่านต้องเป็นนาฬิกาไทย ===")
naive_utc = datetime(2026, 8, 6, 16, 0)          # 16:00 UTC = 23:00 ไทย
check("แปลงเป็นนาฬิกาไทย", to_bangkok(naive_utc).strftime("%d-%m %H:%M"), "06-08 23:00")
check("  ไม่ใช่เวลาธุรกิจ (ซึ่งจะเป็น 07-08 00:00)",
      to_bangkok(naive_utc).strftime("%d-%m %H:%M") == "07-08 00:00", False)
business = datetime(2026, 8, 7, 0, 0, tzinfo=BUSINESS_TZ)
check("  to_bangkok_clock ลดกลับ 1 ชม.",
      to_bangkok_clock(business).strftime("%d-%m %H:%M"), "06-08 23:00")

print("\n=== ไม่มี DST มาทำให้เลื่อน ===")
for month in range(1, 13):
    offset = BUSINESS_TZ.utcoffset(datetime(2026, month, 15))
    if offset != timedelta(hours=8):
        failed += 1
        print(f"[FAIL] เดือน {month} offset เพี้ยน: {offset}")
check("offset คงที่ +8 ทั้ง 12 เดือน", True, True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
