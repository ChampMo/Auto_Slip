# -*- coding: utf-8 -*-
"""ทดสอบด่านเช็คธนาคารผู้รับ และข้อความบอกเหตุผลที่ส่งให้คนกดปุ่ม

เลขบัญชีที่ EasySlip เปิดเผยมีไม่กี่หลัก บัญชีคนละธนาคารจึงทาบกันติดได้
ด่านนี้คัดกรณีนั้นออกก่อนถึงมือคน และข้อความต้องบอกเหตุผลให้ตรงกับความจริง
"""
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

# ต้องตั้งก่อน import อะไรก็ตามที่อ่าน config ไม่งั้นจะไปต่อฐานข้อมูลจริง
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "bank.db")
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111"
for _leak in ("VIP_WE_TOPIC_ID", "VIP_12_TOPIC_ID", "API_ID", "API_HASH"):
    os.environ.pop(_leak, None)

for m in ["pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image", "gspread", "gspread.exceptions",
          "google", "google.oauth2", "google.oauth2.service_account", "googleapiclient",
          "googleapiclient.discovery", "googleapiclient.errors",
          "telegram", "telegram.ext", "telegram.error"]:
    sys.modules.setdefault(m, MagicMock(name=m))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import services.easyslip as easyslip
from services.easyslip import (
    ACCOUNT_BANKS,
    COMPANY_ACCOUNTS,
    bank_agrees,
    normalize_bank_key,
    verify_slip,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r} want {want!r}")


# ── ทุกบัญชีต้องรู้ว่าอยู่ธนาคารไหน ไม่งั้นด่านนี้จะเงียบไปเฉยๆ ──
check("ทุกบัญชีมีธนาคารกำกับ", sorted(set(COMPANY_ACCOUNTS) - set(ACCOUNT_BANKS)), [])
check("ไม่มีธนาคารของบัญชีที่ไม่มีอยู่จริง",
      sorted(set(ACCOUNT_BANKS) - set(COMPANY_ACCOUNTS)), [])

# ── ป้ายชื่อบัญชีขึ้นต้นด้วยธนาคารอะไร ต้องตรงกับที่จดไว้ ──
LABEL_PREFIX = {"SCB": "SCB", "BAY": "BAY", "KB": "KBANK", "KKP": "KKP",
                "GSB": "GSB", "BBL": "BBL", "TTB": "TTB"}
for account, bank in sorted(ACCOUNT_BANKS.items()):
    prefix = account.split("-")[0]
    check(f"{account} ป้ายชื่อกับธนาคารสอดคล้องกัน", LABEL_PREFIX.get(prefix), bank)

# ── อ่านชื่อธนาคารได้ทุกแบบที่สลิปส่งมา ──
check("ตัวย่ออังกฤษ", normalize_bank_key("KBANK"), "KBANK")
check("รหัสตัวเลข", normalize_bank_key("004"), "KBANK")
check("รหัสตัวเลขไม่มี 0 นำหน้า", normalize_bank_key("4"), "KBANK")
check("ชื่อไทย", normalize_bank_key("ธนาคารกสิกรไทย"), "KBANK")
check("ชื่อไทยเต็ม", normalize_bank_key("ธนาคารกสิกรไทย จำกัด (มหาชน)"), "KBANK")
check("เกียรตินาคิน", normalize_bank_key("เกียรตินาคินภัทร"), "KKP")
check("รับ dict ตามที่ API ส่งมา",
      normalize_bank_key({"id": "069", "name": "ธนาคารเกียรตินาคินภัทร", "short": "KKP"}), "KKP")
check("ธนาคารที่เราไม่ได้ถือบัญชี ต้องรู้จักด้วย", normalize_bank_key("กรุงไทย"), "KTB")
check("ไม่รู้จัก = ตอบไม่ได้", normalize_bank_key("ธนาคารดาวอังคาร"), "")
check("ค่าว่าง = ตอบไม่ได้", normalize_bank_key(""), "")
check("None = ตอบไม่ได้", normalize_bank_key(None), "")

# ── เทียบธนาคารกับบัญชี ──
check("ธนาคารเดียวกัน", bank_agrees("KKP-LS", {"short": "KKP"}), True)
check("คนละธนาคาร", bank_agrees("KKP-LS", {"short": "KBANK"}), False)
check("คนละธนาคาร (กรุงไทย)", bank_agrees("KKP-LS", {"short": "KTB"}), False)
check("ธนาคารที่ไม่รู้จัก = ไม่ฟันธง", bank_agrees("KKP-LS", {"short": "???"}), None)
check("บัญชีที่ไม่รู้จัก = ไม่ฟันธง", bank_agrees("ไม่มีจริง", {"short": "KKP"}), None)

# ── ยิงผ่าน verify_slip จริง โดยปลอมคำตอบของ EasySlip ──
def fake_response(bank_short, account_value, receiver_name):
    payload = {
        "status": 200,
        "data": {
            "amount": {"amount": 3000},
            "date": "2026-08-11T17:28:00+07:00",
            "sender": {"account": {"name": {"th": "นาย ประเสริฐ ช"}}},
            "receiver": {
                "bank": {"id": "", "name": "", "short": bank_short},
                "account": {
                    "bank": {"account": account_value},
                    "name": {"th": receiver_name},
                },
            },
        },
    }
    holder = types.SimpleNamespace(json=lambda: payload, status_code=200)
    return holder


def run_verify(bank_short, account_value, receiver_name):
    original = easyslip.requests
    easyslip.requests = types.SimpleNamespace(
        get=lambda *a, **k: fake_response(bank_short, account_value, receiver_name),
        exceptions=original.exceptions if hasattr(original, "exceptions") else MagicMock(),
    )
    try:
        return verify_slip("00410006000001010300402200162231725")
    finally:
        easyslip.requests = original


# เลขทาบ KKP-LS ได้ ชื่อก็ตรง ธนาคารก็ตรง -> ผ่าน
ok = run_verify("KKP", "XXX-X-XX298-4", "นาย เลอสรร บ")
check("ธนาคารตรง -> จับคู่ได้", ok.get("receiver"), "KKP-LS")
check("ธนาคารตรง -> ผ่านด่านบัญชี", ok.get("receiver_bank_matches"), True)
check("ธนาคารตรง -> เหตุผล", ok.get("receiver_reason"), "matched")

# เลขเดียวกันเป๊ะ แต่เป็นสลิปกสิกร -> ต้องไม่ผ่าน (นี่คือช่องโหว่ที่ปิด)
bad_bank = run_verify("KBANK", "XXX-X-XX298-4", "นาย เลอสรร บ")
check("คนละธนาคาร -> ไม่ผ่านด่านบัญชี", bad_bank.get("receiver_bank_matches"), False)
check("คนละธนาคาร -> เหตุผล", bad_bank.get("receiver_reason"), "bank_mismatch")
check("คนละธนาคาร -> ไม่ฟันธงว่าไม่ใช่ของเรา (ต้องให้คนดู)",
      bad_bank.get("receiver_bank_resolved"), False)
check("คนละธนาคาร -> บอกได้ว่าเลขไปตรงกับใบไหน",
      bad_bank.get("receiver_candidate"), "KKP-LS")

# ธนาคารตรง แต่ชื่อคนรับคนละคน -> ต้องไม่ผ่าน
bad_name = run_verify("TTB", "XXX-X-XX001-0", "นาย สุรศักดิ์ ส")
check("ชื่อไม่ตรง -> ไม่ผ่านด่านบัญชี", bad_name.get("receiver_bank_matches"), False)
check("ชื่อไม่ตรง -> เหตุผล", bad_name.get("receiver_reason"), "owner_mismatch")
check("ชื่อไม่ตรง -> เก็บชื่อบนสลิปไว้บอกคนกด",
      bad_name.get("receiver_owner_name"), "นาย สุรศักดิ์ ส")

# ธนาคารที่ EasySlip ไม่ได้ส่งมา ต้องไม่ทำให้ใบที่ถูกต้องตกไป
no_bank = run_verify("", "XXX-X-XX298-4", "นาย เลอสรร บ")
check("ไม่รู้ธนาคาร -> ยังจับคู่ได้เหมือนเดิม", no_bank.get("receiver_bank_matches"), True)

# ── ข้อความต้องบอกเหตุผลให้ตรงกับความจริง ──
from bot.handlers import (  # noqa: E402
    ACCOUNT_CHECK_TITLE_NOT_OURS,
    ACCOUNT_CHECK_TITLE_UNSURE,
    account_check_title,
    build_bank_mismatch_reason,
)

text = build_bank_mismatch_reason(
    ["XXX-X-XX298-4"], [False], ["bank_mismatch"], ["นาย เลอสรร บ"], ["KKP-LS"], ["KBANK"])
check("ข้อความคนละธนาคาร บอกว่าเลขตรง", "matches KKP-LS" in text, True)
check("ข้อความคนละธนาคาร บอกธนาคารบนสลิป", "KBANK" in text, True)
check("ข้อความคนละธนาคาร ไม่พูดว่าไม่ใช่บัญชีบริษัท",
      "is not a company account" in text, False)

text = build_bank_mismatch_reason(
    ["XXX-X-XX001-0"], [False], ["owner_mismatch"], ["นาย สุรศักดิ์ ส"], ["TTB-Jak"], ["TTB"])
check("ข้อความชื่อไม่ตรง บอกชื่อบนสลิป", "นาย สุรศักดิ์ ส" in text, True)
check("ข้อความชื่อไม่ตรง ไม่พูดว่าไม่ใช่บัญชีบริษัท",
      "is not a company account" in text, False)

text = build_bank_mismatch_reason(["2842xxxxxx"], [False], ["ambiguous"], [""], [""], [""])
check("ข้อความชี้ไม่ได้", "more than one company account" in text, True)

text = build_bank_mismatch_reason(["999-9-99999-9"], [False], ["not_company"], [""], [""], [""])
check("ไม่ใช่บัญชีเราจริงๆ ยังพูดแบบเดิม", "is not a company account" in text, True)

check("หัวข้อ: เลขตรงแต่ยังยืนยันไม่ได้",
      account_check_title(["bank_mismatch"]), ACCOUNT_CHECK_TITLE_UNSURE)
check("หัวข้อ: ชื่อไม่ตรง",
      account_check_title(["owner_mismatch"]), ACCOUNT_CHECK_TITLE_UNSURE)
check("หัวข้อ: ไม่ใช่บัญชีเราจริงๆ",
      account_check_title(["not_company"]), ACCOUNT_CHECK_TITLE_NOT_OURS)
check("หัวข้อ: ไม่มีเหตุผลติดมา (ข้อมูลเก่า)",
      account_check_title([]), ACCOUNT_CHECK_TITLE_NOT_OURS)

if failures:
    print("FAILED")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("test_receiver_bank: OK")
