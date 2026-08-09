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

print("\n=== สามบัญชี TTB ที่เลขคล้ายกัน ต้องแยกออกจากกัน ===")
# เคยเป็นจุดที่ข้อมูลชนกัน จึงล็อกไว้กันพลาดซ้ำ
TTB = {"TTB-Nat": "9247580914", "TTB-Jak": "9192560010", "TTB-Yo": "9242185149"}
for name, account in TTB.items():
    check(f"{name} เลขตรงกับที่ลงทะเบียน", known_company_accounts()[name], account)
    check(f"  {name} เปิด 4 ท้าย", match_company_account(reveal_last(account, 4))[0], name)
    check(f"  {name} เปิด 4 ก่อนท้าย", match_company_account(mask_window(account, 5, 4))[0], name)
check("ไม่มีสองใบใช้เลขเดียวกัน", len(set(TTB.values())), 3)

print("\n=== เคสที่โค้ดเดิมพลาด ===")
SCB_CP = "2842525111"      # 10 หลัก
GSB_JAK = "020481919809"   # 12 หลัก


# เคสจริงที่โค้ดเดิมพัง: TTB-Yo = 9242185149 แต่ API เปิดเผย 8514 (ไม่ใช่ 4 ตัวท้าย)
check("4 ตัวก่อนสุดท้าย (แบบที่ API ส่งมาจริง)",
      match_company_account("xxx-x-x8514-x")[0], "TTB-Yo")
check("  GSB-Ativit เปิดกลางเลขเหมือนกัน",
      match_company_account("xxxxxxx9877x")[0], "GSB-Ativit")
check("  4 ตัวท้าย SCB-CP", match_company_account(reveal_last(SCB_CP, 4))[0], "SCB-CP")
check("  4 ตัวก่อนสุดท้าย SCB-CP", match_company_account(mask_window(SCB_CP, 5, 4))[0], "SCB-CP")
check("  API ส่งเลขเต็มมา", match_company_account("284-252-511-1")[0], "SCB-CP")
check("  เปิดเผย 5 หลัก", match_company_account(reveal_last(SCB_CP, 5))[0], "SCB-CP")
check("  เปิดเผย 6 หลัก", match_company_account(mask_window(SCB_CP, 2, 6))[0], "SCB-CP")
check("  บัญชี 12 หลัก (GSB)", match_company_account(reveal_last(GSB_JAK, 4))[0], "GSB-Jak")
check("  GSB เปิดกลางเลข", match_company_account(mask_window(GSB_JAK, 3, 4))[0], "GSB-Jak")
# GSB ทุกบัญชีขึ้นต้น 0204 เหมือนกัน เปิดแค่หัวเลขจึงแยกไม่ออกจริงๆ
check("  GSB เปิดหัวเลข -> กำกวมถูกแล้ว",
      match_company_account(mask_window(GSB_JAK, 0, 4))[1], "ambiguous")

print("\n=== ต้องไม่จับคู่มั่ว ===")
check("บัญชีคนอื่น", match_company_account("xxx-x-x9999-x")[0], None)
check("  ไม่ใช่บัญชีบริษัท (เหตุผล)", match_company_account("999-9-99999-9")[1], "not_company")
check("  ความยาวไม่ตรง ไม่จับคู่ข้ามแบบ",
      match_company_account("xxxxx5111x" + "xx")[0], None)
check("  ปิดหมดทุกหลัก", match_company_account("xxx-x-xxxxx-x")[1], "fully_masked")
check("  ไม่มีเลขบัญชีมาเลย", match_company_account("")[1], "no_account")
check("  None", match_company_account(None)[1], "no_account")

print("\n=== เปิดเผยน้อยจนชี้ไม่ได้ -> ห้ามเดา ===")
# SCB-CP 2842525111 กับ SCB-MT20234 2842520234 ขึ้นต้น 284252 เหมือนกัน
got, reason = match_company_account(mask_window(SCB_CP, 0, 4))
check("เข้าได้หลายบัญชี -> ไม่เลือกให้", got, None)
check("  บอกว่ากำกวม", reason, "ambiguous")
check("  พอเปิดถึงหลักที่ต่างกัน ก็ชี้ได้",
      match_company_account(mask_window(SCB_CP, 0, 7))[0], "SCB-CP")

print("\n=== ความถูกต้องของตารางบัญชี ===")
problems = validate_company_accounts()
print(f"พบปัญหา {len(problems)} ข้อ:")
for p in problems:
    print(f"  • {p}")

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
