"""กลุ่มที่เปิด Topics — ต้องทำงานเฉพาะหัวข้อที่ตั้งไว้เท่านั้น

ทุกหัวข้อในกลุ่มใช้ chat id เดียวกัน ถ้าไม่ดู message_thread_id
บอทจะรับสลิปจากทุกหัวข้อแล้วยอดจะปนกันในชีทโดยไม่มีอะไรเตือน
"""
import importlib
import os
import sys
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["VIP_WE_TOPIC_ID"] = "2"      # กลุ่มแรกจำกัดหัวข้อ
os.environ["VIP_12_TOPIC_ID"] = ""       # กลุ่มที่สองรับทุกหัวข้อ

for name in ["telegram", "telegram.ext", "telegram.error", "requests",
             "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(name, MagicMock(name=name))

import core.config  # noqa: E402
import core.matcher  # noqa: E402
importlib.reload(core.config)
importlib.reload(core.matcher)
from core.matcher import topic_allowed  # noqa: E402

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


print("=== กลุ่มที่จำกัดหัวข้อไว้ที่ 2 ===")
check("หัวข้อ 2 -> รับ", topic_allowed("-100111", 2), True)
check("  เลขมาเป็นข้อความ -> รับ", topic_allowed("-100111", "2"), True)
check("  หัวข้ออื่น -> ไม่รับ", topic_allowed("-100111", 3), False)
check("  General (ไม่มีเลขหัวข้อ) -> ไม่รับ", topic_allowed("-100111", None), False)

print("\n=== กลุ่มที่ไม่ได้จำกัด ===")
check("หัวข้อไหนก็รับ", topic_allowed("-100222", 7), True)
check("  General ก็รับ", topic_allowed("-100222", None), True)

print("\n=== กลุ่มที่ไม่รู้จัก ===")
# ด่านนี้ดูแค่เรื่องหัวข้อ ส่วนกลุ่มแปลกหน้าถูกกันด้วย GROUP_CATEGORY อีกชั้น
check("ปล่อยผ่านด่านนี้ ไปโดนด่านกลุ่มแทน", topic_allowed("-100999", 5), True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
