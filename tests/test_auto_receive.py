"""ทดสอบเงื่อนไข auto receive ด้วยฟังก์ชันจริงใน bot/handlers.py

stub เฉพาะไลบรารีภายนอกที่ยังไม่ได้ติดตั้ง (telegram/gspread/pyzbar/requests ฯลฯ)
เพื่อให้ import bot.handlers ตัวจริงได้ ไม่ต้องคัดลอกลอจิกมาเทสต์
"""
import os
import sys
import types
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
]:
    sys.modules.setdefault(module_name, MagicMock(name=module_name))

# gspread.exceptions.WorksheetNotFound ถูกใช้ใน except -> ต้องเป็น class จริง
gspread_exceptions = types.ModuleType("gspread.exceptions")
gspread_exceptions.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
gspread_exceptions.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = gspread_exceptions
sys.modules["gspread"].exceptions = gspread_exceptions
sys.modules["telegram.error"].TimedOut = type("TimedOut", (Exception,), {})
sys.modules["telegram.error"].NetworkError = type("NetworkError", (Exception,), {})

from bot.handlers import (  # noqa: E402
    amounts_match,
    is_reported_name_verified,
    names_match_in_bank_format,
    resolve_known_bank_value,
    unique_receiver_account,
)
from bot.verification_flow import VerificationDecision, determine_verification_action  # noqa: E402
from services.easyslip import BANK_DROPDOWN_VALUES, match_company_account  # noqa: E402

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


print("=== ข้อ 1: ชื่อ ===")
check("caption มีชื่อเต็ม ตรงกับสลิป", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "ดุลยฤทธิ์ ส"), True)
check("caption มีแค่ชื่อจริง ตรงกับสลิป", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "ดุลยฤทธิ์"), True)
check("caption ชื่อคนละคน", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "สมชาย"), False)
check("caption นามสกุลคนละตัว", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "ดุลยฤทธิ์ ก"), False)
check("caption ไม่มีชื่อเลย (format User)", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], ""), False)
check("caption ใส่ขีดไว้", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "-"), False)
check("caption เว้นวรรคเปล่า", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "   "), False)
check("API อ่านชื่อผู้โอนไม่ได้", is_reported_name_verified(["Unknown"], "ดุลยฤทธิ์"), False)
check("API ไม่ส่งชื่อมาเลย", is_reported_name_verified([], "ดุลยฤทธิ์"), False)
check("หลายสลิป ตรงทุกใบ", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส", "ดุลยฤทธิ์ สมบูรณ์"], "ดุลยฤทธิ์"), True)
check("หลายสลิป ตรงไม่ครบ", is_reported_name_verified(["นาย ดุลยฤทธิ์ ส", "นางสาว ปิยะดา ก"], "ดุลยฤทธิ์"), False)
check("เทียบตรงตัวยังใช้ได้", names_match_in_bank_format("นาย ดุลยฤทธิ์ ส", "ดุลยฤทธิ์ ส"), True)

print("\n=== ข้อ 2: จำนวนเงิน ===")
check("ยอดตรง", amounts_match(100.0, 100.0), True)
check("ยอดตรงคนละทศนิยม", amounts_match(2500, 2500.004), True)
check("ยอดไม่ตรง", amounts_match(100.0, 1000.0), False)
check("caption ไม่ได้บอกยอด", amounts_match(None, 100.0), False)
check("API ไม่ได้ยอด", amounts_match(100.0, None), False)

print("\n=== ข้อ 3: บัญชีผู้รับ 4 หลัก ===")
# KKP-LS = 205-260-2984 เทียบแบบทาบตำแหน่ง ไม่ยึดว่าธนาคารเปิดเผยหลักไหน
check("เปิดเผย 4 ตัวท้าย -> KKP-LS", match_company_account("xxx-xxx-2984")[0], "KKP-LS")
check("เปิดเผยกลางเลข -> KKP-LS", match_company_account("xxx-260-xxxx")[0], "KKP-LS")
check("API ส่งเลขเต็ม -> KKP-LS", match_company_account("205-260-2984")[0], "KKP-LS")
check("บัญชีคนอื่น", match_company_account("xxx-xxx-1234")[0], None)
check("ไม่มีข้อมูลผู้รับ", match_company_account("")[1], "no_account")

print("\n=== ผลรวม: ต้องผ่านครบ 3 ข้อเท่านั้นถึง auto receive ===")
MANUAL = VerificationDecision.MANUAL_REVIEW
RECEIVE = VerificationDecision.AUTO_RECEIVE
REJECT = VerificationDecision.AUTO_REJECT
COMBOS = [
    (True, True, True, RECEIVE, "ผ่านครบ 3 ข้อ"),
    (False, True, True, MANUAL, "ติดแค่ชื่อ -> ถามแอดมิน"),
    (True, False, True, REJECT, "ยอดไม่ตรง -> reject"),
    (True, True, False, REJECT, "บัญชีผู้รับไม่ตรง -> reject"),
    (False, False, True, REJECT, "ชื่อ+ยอดไม่ตรง -> reject"),
    (False, True, False, REJECT, "ชื่อ+บัญชีไม่ตรง -> reject"),
    (True, False, False, REJECT, "ยอด+บัญชีไม่ตรง -> reject"),
    (False, False, False, REJECT, "ไม่ผ่านสักข้อ -> reject"),
]
for name_ok, amount_ok, bank_ok, expected, label in COMBOS:
    got = determine_verification_action(
        api_success=True, amount_matches=amount_ok, name_matches=name_ok,
        bank_matches=bank_ok, multi_slip_batch=False,
    )
    check(f"{label} (ชื่อ={name_ok}, ยอด={amount_ok}, บัญชี={bank_ok})", got, expected)

check("auto receive ต้องมาจากผ่านครบเท่านั้น",
      sum(1 for n, a, b, e, _ in COMBOS if e == RECEIVE), 1)
check("ชื่อไม่ตรงอย่างเดียวห้าม reject เอง",
      determine_verification_action(api_success=True, amount_matches=True, name_matches=False,
                                    bank_matches=True, multi_slip_batch=False), MANUAL)

print("\n=== ข้อมูลไม่พอตัดสิน ต้องถามแอดมิน ไม่ reject เอง ===")
check("หลายสลิปในรูปเดียว แม้ยอดไม่ตรง", determine_verification_action(
    api_success=True, amount_matches=False, name_matches=True,
    bank_matches=True, multi_slip_batch=True), MANUAL)
check("หลายสลิปในรูปเดียว แม้บัญชีไม่ตรง", determine_verification_action(
    api_success=True, amount_matches=True, name_matches=True,
    bank_matches=False, multi_slip_batch=True), MANUAL)
check("API ตรวจไม่ผ่าน", determine_verification_action(
    api_success=False, amount_matches=False, name_matches=False,
    bank_matches=False, multi_slip_batch=False), MANUAL)

print("\n=== เคสจริงจาก 2 รูปที่ส่งมา ===")
# รูปที่ 1: Hi team, User : benz4455 / Amount : THB 100 -> ไม่มีชื่อใน caption
name_ok = is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "")
check("รูป 1 (format User) ข้อชื่อ", name_ok, False)
check("รูป 1 ผลลัพธ์", determine_verification_action(
    api_success=True, amount_matches=True, name_matches=name_ok,
    bank_matches=True, multi_slip_batch=False), VerificationDecision.MANUAL_REVIEW)

# รูปที่ 2: TRANS ID : 0000002 / FULL NAME : ดุลยฤทธิ์ / AMOUNT THB : 100.00
name_ok = is_reported_name_verified(["นาย ดุลยฤทธิ์ ส"], "ดุลยฤทธิ์")
check("รูป 2 (format Trans) ข้อชื่อ", name_ok, True)
check("รูป 2 ผลลัพธ์", determine_verification_action(
    api_success=True, amount_matches=amounts_match(100.0, 100.0), name_matches=name_ok,
    bank_matches=match_company_account("xxx-xxx-2984")[0] is not None, multi_slip_batch=False),
    VerificationDecision.AUTO_RECEIVE)

print("\n=== กดปุ่ม Receive: รู้บัญชีอยู่แล้วต้องไม่ถามซ้ำ ===")


class FakeTxn:
    def __init__(self, receiver_account=None, chat_bank=None):
        self.receiver_account = receiver_account
        self.chat_bank = chat_bank


check("API map บัญชีได้แล้ว (เคสในรูป)", resolve_known_bank_value(FakeTxn(receiver_account="KKP-LS")), "KKP-LS")
check("มีช่องว่างติดมา", resolve_known_bank_value(FakeTxn(receiver_account="  KKP-LS  ")), "KKP-LS")
check("เคยเลือกบัญชีไว้แล้ว", resolve_known_bank_value(FakeTxn(chat_bank="SCB-CP")), "SCB-CP")
check("เลข 4 หลักที่ map ไม่ได้ -> ต้องถาม", resolve_known_bank_value(FakeTxn(receiver_account="2984x")), "")
check("ชื่อร้าน/พร้อมเพย์ -> ต้องถาม", resolve_known_bank_value(FakeTxn(receiver_account="นาย เลอสรร บุญลือ")), "")
check("ค่าขีด -> ต้องถาม", resolve_known_bank_value(FakeTxn(receiver_account="-")), "")
check("ไม่มีข้อมูลเลย -> ต้องถาม", resolve_known_bank_value(FakeTxn()), "")
check("ทุกค่าใน dropdown ต้องถูกยอมรับ",
      all(resolve_known_bank_value(FakeTxn(receiver_account=v)) == v for v in BANK_DROPDOWN_VALUES), True)

print("\n=== หลายสลิป: บัญชีผู้รับตรงกันทุกใบหรือไม่ ===")
check("ใบเดียว", unique_receiver_account(["KKP-LS"]), "KKP-LS")
check("2 ใบเข้าบัญชีเดียวกัน (เคสในรูป)", unique_receiver_account(["KKP-LS", "KKP-LS"]), "KKP-LS")
check("3 ใบเข้าบัญชีเดียวกัน", unique_receiver_account(["KKP-LS", "KKP-LS", "KKP-LS"]), "KKP-LS")
check("มีช่องว่างไม่เท่ากัน", unique_receiver_account([" KKP-LS", "KKP-LS "]), "KKP-LS")
check("เข้าคนละบัญชี -> ต้องถาม", unique_receiver_account(["KKP-LS", "SCB-CP"]), "")
check("อ่านได้บางใบ", unique_receiver_account(["KKP-LS", ""]), "KKP-LS")
check("อ่านไม่ได้เลย", unique_receiver_account(["", "  "]), "")
check("ไม่มีข้อมูล", unique_receiver_account([]), "")

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
