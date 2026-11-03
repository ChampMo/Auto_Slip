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


# --- ชื่อแบบต่างๆ ที่ธนาคารอาจแสดงบนสลิป ต้องผ่านทุกแบบ ไม่งั้นงานหยุดทั้งกลุ่ม ---
for name in ("บจก. พชรมน การช่าง", "บริษัท พชรมน การช่าง จำกัด", "บจก. พชรมน ก",
             "PHACHARAMON KANCHAANG CO.,LTD.", "PHACHARAMON KANCHAANG CO., LTD",
             "Phacharamon Kanchaang Co.,ldt."):
    check(f"KB-PCM ยอมรับ {name}", owner_matches("KB-PCM", name), True)

for name in ("นาย เลิศพงษ์ ร", "นาย เลิศพงษ์ รชตธนานันต์", "MR. LERTPONG R",
             "MR LERTPONG RACHATATHANAN"):
    check(f"KTB-Lertpong ยอมรับ {name}", owner_matches("KTB-Lertpong", name), True)

# --- เลขทาบกันได้แต่คนละคน ต้องไม่ผ่าน ---
check("KB-PCM ปฏิเสธบริษัทอื่น",
      owner_matches("KB-PCM", "บจก. ไทยรุ่งเรือง"), False)
check("  KB-PCM ปฏิเสธบริษัทที่ชื่อคล้าย (มีทอง การช่าง)",
      owner_matches("KB-PCM", "บริษัท มีทอง การช่าง จำกัด"), False)
check("KTB-Lertpong ปฏิเสธชื่ออื่น",
      owner_matches("KTB-Lertpong", "นาย สมชาย ข"), False)
check("  KTB-Lertpong ปฏิเสธชื่อเดียวกันแต่นามสกุลอื่น",
      owner_matches("KTB-Lertpong", "นาย เลิศพงษ์ สุขใจ"), False)
check("ชื่อเจ้าของบัญชีหนึ่ง ไม่ผ่านอีกบัญชี",
      owner_matches("KTB-Lertpong", "บจก. พชรมน การช่าง"), False)

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
check("ชื่อผู้รับว่าง = ไม่ตรวจ", owner_matches("KB-PCM", ""), True)
check("ชื่อผู้รับเป็น None = ไม่ตรวจ", owner_matches("KB-PCM", None), True)

# --- การหั่นคำ ---
check("ตัดคำนำหน้าไทย", owner_tokens("นาย เลอสรร บ"), ["เลอสรร", "บ"])
check("ตัดคำนำหน้าอังกฤษ", owner_tokens("MR. LOESAN B"), ["loesan", "b"])
check("ตัด บจก.", owner_tokens("บจก. บิลท์สเปค"), ["บิลท์สเปค"])
check("ตัด บริษัท/จำกัด", owner_tokens("บริษัท มีทอง จำกัด"), ["มีทอง"])
check("ตัด CO.,LTD", owner_tokens("BUILTSPAC CO.,LTD"), ["builtspac"])
check("ตัด Co., Ltd. ของ KB-PCM", owner_tokens("Phacharamon Kanchaang Co., Ltd."),
      ["phacharamon", "kanchaang"])

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
