"""ทดสอบการจับคู่บัญชีผู้รับ กับทุกรูปแบบการปิดบังเลขที่ธนาคารอาจส่งมา"""
import os
import sys
import types
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
for m in ["requests", "gspread", "gspread.exceptions", "google", "google.oauth2",
          "google.oauth2.service_account", "googleapiclient", "googleapiclient.discovery",
          "googleapiclient.errors", "telegram", "telegram.ext", "telegram.error",
          "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, MagicMock(name=m))
ge = types.ModuleType("gspread.exceptions")
ge.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
ge.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = ge

from services.easyslip import (  # noqa: E402
    COMPANY_ACCOUNTS,
    known_company_accounts,
    match_company_account,
    normalize_account_value,
    validate_company_accounts,
)

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def mask_window(account: str, start: int, length: int = 4) -> str:
    """สร้างเลขแบบปิดบัง โดยเปิดเผยเฉพาะช่วงที่กำหนด"""
    return "".join(ch if start <= i < start + length else "x"
                   for i, ch in enumerate(account))


def reveal_last(account: str, count: int) -> str:
    """เปิดเผยเฉพาะหลักท้ายสุดตามจำนวนที่ระบุ"""
    return mask_window(account, len(account) - count, count)


print("=== ตัดตัวคั่นและแปลงตัวปิดบังให้เป็นรูปเดียวกัน ===")
check("รูปแบบมาสก์ของ EasySlip", normalize_account_value("xxx-x-x5678-x"), "xxxxx5678x")
check("  เลขเต็มมีขีด", normalize_account_value("284-252-511-1"), "2842525111")
check("  ใช้ * ปิดบัง", normalize_account_value("***-*-*5678-*"), "xxxxx5678x")
check("  ตัวพิมพ์ใหญ่", normalize_account_value("XXX-X-X5678-X"), "xxxxx5678x")
check("  มีช่องว่าง", normalize_account_value("284 252 511 1"), "2842525111")
check("  ค่าว่าง", normalize_account_value(""), "")
check("  None", normalize_account_value(None), "")

print("\n=== หัวใจ: ปิดบังตำแหน่งไหนก็จับคู่ถูก ===")
# ทดสอบทุกบัญชี กับทุกตำแหน่งที่ธนาคารอาจเลือกเปิดเผย
KNOWN = known_company_accounts()
per_account_fail = 0
windows = 0
for name, account in KNOWN.items():
    for start in range(len(account) - 3):
        masked = mask_window(account, start)
        got, reason = match_company_account(masked)
        windows += 1
        # ยอมรับ ambiguous ได้ เพราะบางช่วงบัญชีคนละใบมีเลขเหมือนกันจริง
        if got != name and reason != "ambiguous":
            per_account_fail += 1
            print(f"[FAIL] {name} มาสก์ {masked} -> {got!r} ({reason})")
check(f"{len(KNOWN)} บัญชี x {windows} ตำแหน่ง ไม่มีจับคู่ผิดเลย", per_account_fail, 0)

print("\n=== บัญชีที่ยังไม่รู้เลข ต้องไม่ถูกจับคู่ ===")
# ทดสอบ "กลไก" ไม่ใช่ว่าตอนนี้บังเอิญมีบัญชีรอเลขอยู่หรือไม่
# (ถ้าผูกกับสภาพปัจจุบัน เทสต์จะพังทุกครั้งที่เติมเลขบัญชีครบ)
import services.easyslip as es  # noqa: E402

saved_table = dict(es.COMPANY_ACCOUNTS)
try:
    es.COMPANY_ACCOUNTS["ZZ-Pending"] = es.UNKNOWN_ACCOUNT
    check("บัญชีที่ไม่มีเลข ไม่เข้ารายการจับคู่",
          "ZZ-Pending" in es.known_company_accounts(), False)
    check("  แต่ผลตรวจต้องฟ้องให้รู้",
          any("ZZ-Pending" in problem for problem in es.validate_company_accounts()), True)
finally:
    es.COMPANY_ACCOUNTS.clear()
    es.COMPANY_ACCOUNTS.update(saved_table)

check("  คืนตารางกลับเรียบร้อย", len(es.COMPANY_ACCOUNTS), len(saved_table))
check("ตอนนี้ทุกบัญชีมีเลขครบแล้ว",
      sorted(set(COMPANY_ACCOUNTS) - set(known_company_accounts())), [])

print("\n=== บัญชีที่ใช้อยู่ตอนนี้ ===")
KB_PCM = "2372511844"        # กสิกรไทย 237-251-1844  บจก. พชรมน การช่าง
KTB_LERT = "7950771410"      # กรุงไทย  795-077-1410  เลิศพงษ์ รชตธนานันต์
check("มีแค่สองบัญชี", sorted(COMPANY_ACCOUNTS), ["KB-PCM", "KTB-Lertpong"])
check("KB-PCM เลขตรงกับที่ลงทะเบียน", known_company_accounts()["KB-PCM"], KB_PCM)
check("KTB-Lertpong เลขตรงกับที่ลงทะเบียน", known_company_accounts()["KTB-Lertpong"], KTB_LERT)

for name, account in (("KB-PCM", KB_PCM), ("KTB-Lertpong", KTB_LERT)):
    check(f"  {name} เปิด 4 ท้าย", match_company_account(reveal_last(account, 4))[0], name)
    check(f"  {name} เปิด 4 ก่อนท้าย", match_company_account(mask_window(account, 5, 4))[0], name)
    check(f"  {name} เปิดเผย 6 หลัก", match_company_account(mask_window(account, 2, 6))[0], name)

# รูปแบบมาสก์ที่ธนาคารส่งมาจริง (xxx-x-x####-x)
check("KBank แบบมาสก์จริง", match_company_account("xxx-x-x1184-x")[0], "KB-PCM")
check("KTB แบบมาสก์จริง", match_company_account("xxx-x-x7141-x")[0], "KTB-Lertpong")
check("API ส่งเลขเต็มมา (KB)", match_company_account("237-251-1844")[0], "KB-PCM")
check("API ส่งเลขเต็มมา (KTB)", match_company_account("795-077-1410")[0], "KTB-Lertpong")

print("\n=== บัญชีที่เลิกใช้แล้ว ต้องไม่ถูกจับคู่อีก ===")
check("SCB-CP เดิม 284-252-511-1", match_company_account("284-252-511-1")[1], "not_company")
check("  TTB-Yo เดิม (มาสก์แบบที่เคยส่งมา)", match_company_account("xxx-x-x8514-x")[1], "not_company")
check("  GSB 12 หลักเดิม", match_company_account("020-481-91980-9")[1], "not_company")

print("\n=== ต้องไม่จับคู่มั่ว ===")
check("บัญชีคนอื่น", match_company_account("xxx-x-x9999-x")[0], None)
check("  ไม่ใช่บัญชีบริษัท (เหตุผล)", match_company_account("999-9-99999-9")[1], "not_company")
check("  ความยาวไม่ตรง ไม่จับคู่ข้ามแบบ",
      match_company_account("xxxxx1184x" + "xx")[0], None)
check("  ปิดหมดทุกหลัก", match_company_account("xxx-x-xxxxx-x")[1], "fully_masked")
check("  ไม่มีเลขบัญชีมาเลย", match_company_account("")[1], "no_account")
check("  None", match_company_account(None)[1], "no_account")

print("\n=== เปิดเผยน้อยจนชี้ไม่ได้ -> ห้ามเดา ===")
# ทดสอบกลไก ด้วยบัญชีสมมติที่ขึ้นต้นเหมือน KB-PCM (บัญชีจริงตอนนี้ไม่มีคู่ไหนขึ้นต้นเหมือนกัน)
saved_table = dict(es.COMPANY_ACCOUNTS)
try:
    es.COMPANY_ACCOUNTS["ZZ-Twin"] = "237-251-9999"
    got, reason = match_company_account(mask_window(KB_PCM, 0, 4))
    check("เข้าได้หลายบัญชี -> ไม่เลือกให้", got, None)
    check("  บอกว่ากำกวม", reason, "ambiguous")
    check("  พอเปิดถึงหลักที่ต่างกัน ก็ชี้ได้",
          match_company_account(mask_window(KB_PCM, 0, 7))[0], "KB-PCM")
finally:
    es.COMPANY_ACCOUNTS.clear()
    es.COMPANY_ACCOUNTS.update(saved_table)

print("\n=== ชื่อบัญชียาวเกินปุ่ม Telegram ต้องถูกฟ้องตอนบูต ===")
saved_table = dict(es.COMPANY_ACCOUNTS)
try:
    es.COMPANY_ACCOUNTS["KB-ชื่อยาวมากเกินไป"] = "111-111-1111"
    check("ป้ายยาวเกิน 64 ไบต์ของ callback_data ถูกฟ้อง",
          any("ไบต์" in p and "KB-ชื่อยาวมากเกินไป" in p for p in es.validate_company_accounts()), True)
    check("  บัญชีที่ไม่มีธนาคาร/ชื่อเจ้าของกำกับ ถูกฟ้องด้วย",
          sum("KB-ชื่อยาวมากเกินไป" in p for p in es.validate_company_accounts()), 3)
finally:
    es.COMPANY_ACCOUNTS.clear()
    es.COMPANY_ACCOUNTS.update(saved_table)

for name in COMPANY_ACCOUNTS:
    longest = f"bank_{'f' * 40}_{name}"
    check(f"{name} ใส่ปุ่มได้แม้รหัสสลิปยาวสุด", len(longest.encode("utf-8")) <= 64, True)

print("\n=== ความถูกต้องของตารางบัญชี ===")
problems = validate_company_accounts()
print(f"พบปัญหา {len(problems)} ข้อ:")
for p in problems:
    print(f"  • {p}")
check("ตารางบัญชีปัจจุบันไม่มีปัญหาเลย", problems, [])

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
