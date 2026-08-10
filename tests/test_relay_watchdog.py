"""ตัวเฝ้าดูตัวฟัง — ต้องแจ้งครั้งเดียวตอนหลุด และแจ้งอีกครั้งตอนกลับมา

ตัวฟังหลุดแล้วเงียบคือความเสี่ยงที่ใหญ่ที่สุด แต่ถ้าแจ้งซ้ำทุก 5 นาที
กลุ่มจะรกจนคนเลิกอ่าน กลายเป็นเงียบอีกแบบหนึ่ง
"""
import os
import sys
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"

for name in ["telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
             "google", "google.oauth2", "google.oauth2.service_account",
             "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
             "requests", "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image",
             "apscheduler", "apscheduler.schedulers", "apscheduler.schedulers.background",
             "apscheduler.triggers", "apscheduler.triggers.cron", "telethon"]:
    sys.modules.setdefault(name, MagicMock(name=name))

import scheduler as sched  # noqa: E402
import services.relay as relay  # noqa: E402

failed = 0
sent = []


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


sched.send_telegram_message = lambda chat_id, text: sent.append(text)


def set_state(enabled, connected):
    relay.relay_status = lambda: {"enabled": enabled, "connected": connected,
                                  "last_seen": None, "last_seen_at": None, "last_error": ""}
    sys.modules["services.relay"].relay_status = relay.relay_status


print("=== ตัวฟังปิดอยู่ ===")
sched._relay_alerted["down"] = False
set_state(False, False); sent.clear()
sched.watch_relay()
check("ไม่แจ้งอะไร", len(sent), 0)

print("\n=== ตัวฟังหลุด ===")
set_state(True, False); sent.clear()
sched.watch_relay()
check("แจ้งเตือน", len(sent), 2)          # แจ้งทุกกลุ่มที่ลงทะเบียน
check("  บอกว่าหลุด", "ตัวฟังข้อความหลุด" in sent[0], True)
check("  บอกว่าต้องทำอะไรต่อ", "ฟอร์เวิร์ด" in sent[0], True)

print("\n=== ยังหลุดอยู่รอบถัดไป ===")
sent.clear()
sched.watch_relay()
check("ไม่แจ้งซ้ำ", len(sent), 0)

print("\n=== กลับมาแล้ว ===")
set_state(True, True); sent.clear()
sched.watch_relay()
check("แจ้งว่ากลับมา", len(sent), 2)
check("  ข้อความถูกต้อง", "กลับมาทำงาน" in sent[0], True)

print("\n=== ปกติต่อเนื่อง ===")
sent.clear()
sched.watch_relay()
check("ไม่แจ้งอะไรอีก", len(sent), 0)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
