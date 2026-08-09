"""ทดสอบเวลาโอนที่อ่านจากสลิป — ช่อง Time ในชีทต้องเป็นเวลาที่เงินออกจริง

ไม่ใช่เวลาที่กดปุ่มบันทึก เพราะสลิปที่ค้างข้ามวันแล้วเพิ่งมากด
จะได้เวลาที่ไม่ตรงกับหน้าสลิปเลย
"""
import os
import sys
import types
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import MagicMock

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

from bot.verification_flow import (  # noqa: E402
    VerificationDecision,
    determine_verification_action,
)
from core.captions import (  # noqa: E402
    parse_typed_date_and_times,
    parse_typed_times,
    resolve_year,
)
from services.easyslip import parse_transfer_time  # noqa: E402
from services.gsheets import format_clock, format_transfer_times  # noqa: E402

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


print("=== อ่านเวลาโอนจาก EasySlip ===")
# เคสจริงจาก api_raw_data ของสลิป K+ ใบหนึ่ง
real = parse_transfer_time("2026-08-08T19:04:20+07:00")
check("อ่านเวลาได้", format_clock(real), "19:04")
check("  หลังเที่ยงคืน ตัดศูนย์นำหน้า",
      format_clock(parse_transfer_time("2026-08-09T00:18:00+07:00")), "0:18")
check("  เวลาแบบ Z (UTC) แปลงเป็นเวลาไทย",
      format_clock(parse_transfer_time("2026-08-08T12:04:20Z")), "19:04")
check("  ไม่มีเขตเวลา ถือเป็นเวลาไทย",
      format_clock(parse_transfer_time("2026-08-09T00:18:00")), "0:18")
check("  ค่าว่าง", parse_transfer_time(""), None)
check("  อ่านไม่ออก", parse_transfer_time("ไม่ใช่เวลา"), None)
check("  ไม่มีคีย์ date เลย", parse_transfer_time(None), None)

print("\n=== หลายสลิปในรูปเดียว ===")
later = parse_transfer_time("2026-08-09T00:37:00+07:00")
earlier = parse_transfer_time("2026-08-09T00:18:00+07:00")
check("เรียงตามเวลาแล้วต่อด้วยจุลภาค",
      format_transfer_times([later, earlier]), "0:18, 0:37")
check("  ใบเดียว", format_transfer_times([earlier]), "0:18")
check("  อ่านไม่ได้สักใบ -> ว่าง", format_transfer_times([None, None]), "")
check("  อ่านได้บางใบ -> เอาเท่าที่ได้", format_transfer_times([earlier, None]), "0:18")
check("  ลิสต์ว่าง", format_transfer_times([]), "")

print("\n=== แอดมินพิมพ์เวลาเอง ===")
for label, typed, expected in [
    ("รูปแบบปกติ", "0:18", "0:18"),
    ("  มีศูนย์นำหน้า", "00:18", "0:18"),
    ("  หลายใบ", "0:18, 0:37", "0:18, 0:37"),
    ("  ใช้จุดคั่น", "0.18", "0:18"),
    ("  มีข้อความไทยปน", "เวลา 23:59 น.", "23:59"),
    ("  ชั่วโมงเกินจริง", "25:00", None),
    ("  นาทีเกินจริง", "0:60", None),
    ("  ไม่ใช่เวลา", "abc", None),
    ("  พิมพ์ยอดมาแทนเวลา", "1500", None),
    ("  มีเลขอื่นปนที่ไม่ใช่เวลา", "0:18 และ 900", None),
    ("  ค่าว่าง", "", None),
]:
    check(label, parse_typed_times(typed), expected)

print("=== วันที่บนสลิปไทย ===")
# สลิปเขียนปีเป็น พ.ศ. สองหลัก ถ้าแปลงผิด การกันสลิปซ้ำจะเทียบคนละวัน
for label, typed, expected in [
    ("วัน/เดือน/ปี พ.ศ. ย่อ", "8/8/69 0:18", (date(2026, 8, 8), "0:18")),
    ("  เดือนภาษาไทย", "8 ส.ค. 69 0:18", (date(2026, 8, 8), "0:18")),
    ("  เดือนเต็ม + ปีเต็ม", "8 สิงหาคม 2569 0:18", (date(2026, 8, 8), "0:18")),
    ("  แบบ ISO", "2026-08-08 19:04", (date(2026, 8, 8), "19:04")),
    ("  จุดคั่นวัน ต้องไม่อ่านเป็นเวลา", "08.08.69 0:18", (date(2026, 8, 8), "0:18")),
    ("  หลายใบวันเดียวกัน", "8/8/69 0:18, 0:37", (date(2026, 8, 8), "0:18, 0:37")),
    ("  พิมพ์มาแค่เวลา ยังรับได้", "0:18", (None, "0:18")),
    ("  วันที่ไม่มีจริง", "31/2/69 0:18", (None, None)),
    ("  ไม่ใช่วันและเวลา", "abc", (None, None)),
    ("  มีวันแต่ไม่มีเวลา", "8/8/69", (date(2026, 8, 8), None)),
]:
    check(label, parse_typed_date_and_times(typed), expected)

print("\n=== แปลงปี ===")
TODAY = date(2026, 8, 9)
for label, raw, expected in [
    ("พ.ศ. ย่อ 69", 69, 2026),
    ("  ค.ศ. ย่อ 26", 26, 2026),
    ("  พ.ศ. เต็ม", 2569, 2026),
    ("  ค.ศ. เต็ม", 2026, 2026),
    ("  ปีที่แล้วแบบ พ.ศ.", 68, 2025),
]:
    check(label, resolve_year(raw, TODAY), expected)

print("\n=== ไม่รู้เวลาโอน ต้องไม่ auto receive ===")
passing = dict(api_success=True, amount_matches=True, name_matches=True, bank_matches=True)
check("ครบทุกข้อ + รู้เวลา -> รับเอง",
      determine_verification_action(**passing, transfer_time_known=True),
      VerificationDecision.AUTO_RECEIVE)
check("  ครบทุกข้อ แต่ไม่รู้เวลา -> ให้คนดู",
      determine_verification_action(**passing, transfer_time_known=False),
      VerificationDecision.MANUAL_REVIEW)
# เรื่องเวลาเป็นแค่ข้อมูลไม่ครบ ห้ามไปกลบเหตุปฏิเสธที่หนักกว่า
check("  บัญชีไม่ใช่ของเรา ยัง reject แม้ไม่รู้เวลา",
      determine_verification_action(api_success=True, amount_matches=True, name_matches=True,
                                    bank_matches=False, bank_resolved=True,
                                    transfer_time_known=False),
      VerificationDecision.AUTO_REJECT)
check("  ยอดไม่ตรง ยัง reject แม้ไม่รู้เวลา",
      determine_verification_action(api_success=True, amount_matches=False, name_matches=True,
                                    bank_matches=True, transfer_time_known=False),
      VerificationDecision.AUTO_REJECT)
check("  ค่าเริ่มต้นคือรู้เวลา (ของเดิมไม่พัง)",
      determine_verification_action(**passing), VerificationDecision.AUTO_RECEIVE)

print("\n=== ชีทต้องใช้เวลาโอน ไม่ใช่เวลาที่กด ===")
# จำลองสิ่งที่ append_to_sheet ทำกับช่อง Time
BUSINESS = dt_timezone(timedelta(hours=8), "UTC+8")


def time_cell(transfer_time_text, pressed_at):
    return (transfer_time_text or "").strip() or format_clock(pressed_at)


pressed = datetime(2026, 8, 9, 21, 5, tzinfo=BUSINESS)   # กดตอนดึกของอีกวัน
check("มีเวลาโอน -> ใช้เวลาโอน", time_cell("0:18", pressed), "0:18")
check("  หลายใบ -> ใส่ทั้งสอง", time_cell("0:18, 0:37", pressed), "0:18, 0:37")
check("  ไม่มีเวลาโอนเลย -> ใช้เวลาที่บันทึกแทน ไม่ปล่อยว่าง",
      time_cell(None, pressed), format_clock(pressed))
check("  ค่าว่างล้วน -> ใช้เวลาที่บันทึกแทน", time_cell("   ", pressed), format_clock(pressed))

print("\n=== คอลัมน์ในฐานข้อมูลต้องยาวพอ ===")
from database.models import TRANSFER_TIME_MAX  # noqa: E402

many = format_transfer_times([earlier] * 20)
check("20 ใบยังไม่ล้นคอลัมน์", len(many) <= TRANSFER_TIME_MAX, True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
