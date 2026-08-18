# -*- coding: utf-8 -*-
"""ทดสอบด่านเช็คชื่อเจ้าของบัญชี (กันเลข 4 ตัวท้ายชนกันข้ามคน)

ชื่อที่ใช้เทสมาจากสลิปจริงใน DB ทั้งหมด รวมทั้งแบบที่ธนาคารย่อนามสกุล
เหลือตัวเดียว ('นาย เลอสรร บ') และแบบภาษาอังกฤษ ('MR. LOESAN B')
"""
import os
import sys
from unittest.mock import MagicMock

for m in ["requests", "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, MagicMock(name=m))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.easyslip import (
    ACCOUNT_OWNERS,
    COMPANY_ACCOUNTS,
    known_company_accounts,
    match_company_account,
    owner_matches,
    owner_tokens,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r} want {want!r}")


def aliases_of(account):
    value = ACCOUNT_OWNERS.get(account) or []
    return [value] if isinstance(value, str) else list(value)


# --- ชื่อจริงจากสลิปที่รับเข้าไปแล้ว ต้องผ่านทุกใบ ไม่งั้นงานหยุดทั้งกลุ่ม ---
for name in ("นาย เลอสรร บ", "MR. LOESAN B", "นาย เลอสรร บุญลือ"):
    check(f"KKP-LS ยอมรับ {name}", owner_matches("KKP-LS", name), True)

for name in ("บจก. บ", "บจก. บิลท์สเปค", "BUILTSPAC C"):
    check(f"KB-BS ยอมรับ {name}", owner_matches("KB-BS", name), True)

check("SCB-MT20234 ยอมรับชื่อเต็ม",
      owner_matches("SCB-MT20234", "บริษัท มีทอง ก"), True)
check("SCB-Yo ยอมรับชื่อย่อ",
      owner_matches("SCB-Yo", "นาย อนุชา ส"), True)
# ธนาคารสะกดยาวกว่าที่เราจดไว้ ('อติวิช' -> 'อติวิชญ์') ต้องยังผ่าน
check("GSB-Ativit ยอมรับชื่อที่ยาวกว่าที่จด",
      owner_matches("GSB-Ativit", "นาย อติวิชญ์ ฉ"), True)

# --- เคสที่เจอจริง: เลขทาบกันได้แต่คนละคน ต้องไม่ผ่าน ---
check("TTB-Jak ปฏิเสธผู้รับคนอื่น",
      owner_matches("TTB-Jak", "นาย สุรศักดิ์ ส"), False)
check("KKP-LS ปฏิเสธชื่ออื่น",
      owner_matches("KKP-LS", "นาย สมชาย ข"), False)
check("KB-BS ปฏิเสธบริษัทอื่น",
      owner_matches("KB-BS", "บจก. ไทยรุ่งเรือง"), False)

# --- ทุกบัญชีต้องมีชื่อกำกับ และต้องยอมรับชื่อของตัวเองทุกแบบ ---
check("ทุกบัญชีมีชื่อกำกับครบ",
      sorted(set(COMPANY_ACCOUNTS) - {a for a in ACCOUNT_OWNERS if aliases_of(a)}), [])
check("ไม่มีชื่อของบัญชีที่ไม่มีอยู่จริง",
      sorted(set(ACCOUNT_OWNERS) - set(COMPANY_ACCOUNTS)), [])

for account in sorted(COMPANY_ACCOUNTS):
    for alias in aliases_of(account):
        tokens = owner_tokens(alias)
        check(f"{account} ยอมรับชื่อตัวเอง {alias!r}",
              owner_matches(account, alias), True)
        if len(tokens) >= 2:
            # ธนาคารไทยย่อนามสกุลเหลือตัวเดียวเป็นเรื่องปกติ
            shortened = tokens[0] + " " + tokens[1][0]
            check(f"{account} ยอมรับชื่อย่อ {shortened!r}",
                  owner_matches(account, shortened), True)


def owner_identity(account):
    return frozenset(tuple(owner_tokens(a)) for a in aliases_of(account))


# --- ชื่อของคนอื่นต้องไม่หลุดผ่าน (ข้ามคู่ที่เป็นเจ้าของคนเดียวกันหลายบัญชี) ---
leaks = []
for account in sorted(COMPANY_ACCOUNTS):
    for other in sorted(COMPANY_ACCOUNTS):
        if account == other or owner_identity(account) == owner_identity(other):
            continue
        for alias in aliases_of(other):
            if owner_matches(account, alias):
                leaks.append(f"{account} ยอมรับชื่อของ {other} ({alias})")
check("ไม่มีชื่อคนอื่นหลุดผ่าน", leaks, [])

# --- บัญชีที่ไม่มีในตาราง = ไม่ตรวจ ---
check("บัญชีที่ไม่มีในตาราง = ไม่ตรวจ",
      owner_matches("ไม่รู้จัก", "นาย ใครก็ได้ ก"), True)

# --- สลิปไม่มีชื่อผู้รับ (วอลเล็ตบางเจ้า) ไม่ใช่หลักฐานว่าผิดคน ---
check("ชื่อผู้รับว่าง = ไม่ตรวจ", owner_matches("KKP-LS", ""), True)
check("ชื่อผู้รับเป็น None = ไม่ตรวจ", owner_matches("KKP-LS", None), True)

# --- การหั่นคำ ---
check("ตัดคำนำหน้าไทย", owner_tokens("นาย เลอสรร บ"), ["เลอสรร", "บ"])
check("ตัดคำนำหน้าอังกฤษ", owner_tokens("MR. LOESAN B"), ["loesan", "b"])
check("ตัด บจก.", owner_tokens("บจก. บิลท์สเปค"), ["บิลท์สเปค"])
check("ตัด บริษัท/จำกัด", owner_tokens("บริษัท มีทอง จำกัด"), ["มีทอง"])
check("ตัด CO.,LTD", owner_tokens("BUILTSPAC CO.,LTD"), ["builtspac"])

# --- เลขบัญชี: ห้ามจับคู่ผิดใบ แม้ธนาคารจะเปิดเผยแค่ 4 หลักติดกัน ---
wrong_matches = []
for account, digits in sorted(known_company_accounts().items()):
    for start in range(len(digits) - 3):
        masked = "x" * start + digits[start:start + 4] + "x" * (len(digits) - start - 4)
        matched, reason = match_company_account(masked)
        # ชี้ไม่ได้ (ambiguous) ยอมรับได้ เพราะจะส่งให้คนตัดสิน
        # แต่ "ชี้เป็นบัญชีอื่น" ยอมไม่ได้ เงินจะลงผิดบัญชี
        if matched is not None and matched != account:
            wrong_matches.append(f"{account} ถูกชี้เป็น {matched} ({masked})")
check("ไม่มีเลขบัญชีใบไหนถูกชี้เป็นใบอื่น", wrong_matches, [])

# --- เลขบัญชีต้องไม่ซ้ำกัน ---
digits_seen = {}
dupes = []
for account, digits in sorted(known_company_accounts().items()):
    if digits in digits_seen:
        dupes.append(f"{account} เลขซ้ำกับ {digits_seen[digits]}")
    digits_seen[digits] = account
check("ไม่มีเลขบัญชีซ้ำกัน", dupes, [])

if failures:
    print("FAILED")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("test_owner_names: OK")
