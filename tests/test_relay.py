"""ตัวฟังข้อความจากบอทอื่น — ต้องรับเฉพาะที่ควรรับ

จุดที่พลาดแล้วเจ็บที่สุดคือรับข้อความของตัวเอง (วนไม่รู้จบ)
กับรับข้อความของคน (บอทหลักเห็นอยู่แล้ว จะกลายเป็นตรวจซ้ำสองรอบต่อสลิปหนึ่งใบ)
"""
import importlib
import os
import sys
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["BOT_TOKEN"] = "555000:AAAAtestTOKEN"
os.environ["RELAY_API_ID"] = ""
os.environ["RELAY_API_HASH"] = ""
# config รับ API_ID/API_HASH เป็นชื่อสำรองด้วย ต้องล้างทั้งคู่
os.environ["API_ID"] = ""
os.environ["API_HASH"] = ""

for name in ["telegram", "telegram.ext", "telegram.error", "requests",
             "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image", "telethon", "telethon.events"]:
    sys.modules.setdefault(name, MagicMock(name=name))

import core.config  # noqa: E402
import core.matcher  # noqa: E402
importlib.reload(core.config)
importlib.reload(core.matcher)
import services.relay as relay  # noqa: E402
importlib.reload(relay)

failed = 0
OTHER_BOT = 777111
HUMAN = 424242
OWN_BOT = 555000


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


print("=== รับจากใครบ้าง (ไม่จำกัดชื่อบอท) ===")
check("บอทตัวอื่นในกลุ่มที่ลงทะเบียน -> รับ",
      relay.should_handle("-100111", OTHER_BOT, True, set()), True)
check("  คนส่งเอง -> ไม่รับ (บอทหลักเห็นเองแล้ว)",
      relay.should_handle("-100111", HUMAN, False, set()), False)
check("  ข้อความของ Auto Slip เอง -> ไม่รับ (กันวนไม่รู้จบ)",
      relay.should_handle("-100111", OWN_BOT, True, set()), False)
check("  กลุ่มที่ไม่ได้ลงทะเบียน -> ไม่รับ",
      relay.should_handle("-100999", OTHER_BOT, True, set()), False)

print("\n=== จำกัดเฉพาะบอทที่ระบุ ===")
allowed = {"777111"}
check("อยู่ในรายชื่อ -> รับ", relay.should_handle("-100111", 777111, True, allowed), True)
check("  ไม่อยู่ในรายชื่อ -> ไม่รับ",
      relay.should_handle("-100111", 888222, True, allowed), False)

print("\n=== เปิด/ปิดตัวฟัง ===")
check("ไม่ได้ตั้งค่า -> ปิดอยู่", relay.relay_enabled(), False)
check("  /health รายงานได้", relay.relay_status()["enabled"], False)

print("\n=== หมายเลขหัวข้อ ===")
plain = MagicMock(); plain.reply_to = None
check("ข้อความธรรมดา -> ไม่มีหัวข้อ", relay._topic_of(plain), None)
in_topic = MagicMock()
in_topic.reply_to = MagicMock(forum_topic=True, reply_to_top_id=2, reply_to_msg_id=9)
check("  อยู่ในหัวข้อ 2", relay._topic_of(in_topic), 2)
reply_only = MagicMock()
reply_only.reply_to = MagicMock(forum_topic=False, reply_to_top_id=None, reply_to_msg_id=9)
check("  reply ธรรมดาไม่ใช่หัวข้อ", relay._topic_of(reply_only), None)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
