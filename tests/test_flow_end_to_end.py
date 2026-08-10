from datetime import datetime as _dt, timedelta as _td, timezone as _tz
"""ทดสอบ process_slip_group ตัวจริงตั้งแต่รับ QR -> บวกยอด -> ตัดสิน -> ข้อความที่ตอบกลับ

ใช้ sqlite ไฟล์จริง + ปลอมเฉพาะ EasySlip API กับการเขียน Google Sheets
"""
import asyncio
import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "e2e.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
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

gspread_exceptions = types.ModuleType("gspread.exceptions")
gspread_exceptions.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
gspread_exceptions.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = gspread_exceptions
sys.modules["gspread"].exceptions = gspread_exceptions
sys.modules["telegram.error"].TimedOut = type("TimedOut", (Exception,), {})
sys.modules["telegram.error"].NetworkError = type("NetworkError", (Exception,), {})

import bot.handlers as h  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402
from database.models import Transaction, UsedQR  # noqa: E402

init_db()

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


# ---- ปลอม EasySlip ----
SLIP_AMOUNTS = {}
SLIP_RECEIVERS = {}  # payload -> บัญชีผู้รับ (ไม่ระบุ = KKP-LS)


def fake_verify_slip(payload):
    amount = SLIP_AMOUNTS.get(payload)
    if amount is None:
        return {"success": False, "error": "INVALID_SLIP",
                "user_message": "Slip not found in bank records.", "payload_amount": None}
    return {
        "success": True,
        "amount": amount,
        "payload_amount": amount,
        "sender": "นาย ดุลยฤทธิ์ ส",
        "receiver": SLIP_RECEIVERS.get(payload, "KKP-LS"),
        "receiver_bank_code": "2984",
        "receiver_bank_matches": True,
        "transfer_at": _dt(2026, 8, 9, 0, 18, tzinfo=_tz(_td(hours=7))),
        "raw_data": {"payload": payload},
    }


h.verify_slip = fake_verify_slip
h.append_to_sheet = lambda txn: (True, "")


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, reply_to_message_id=None, reply_markup=None,
                           message_thread_id=None):
        self.messages.append({"text": text, "reply_to": reply_to_message_id, "keyboard": reply_markup})


CAPTION_400 = "TRANS ID : 0000009\nFULL NAME : ดุลยฤทธิ์\nAMOUNT THB : 400.00"


async def run_group(qr_list, caption, photo_count, msg_id):
    bot = FakeBot()
    await h.process_slip_group(bot, "-100111", msg_id, caption, qr_list, photo_count)
    return bot.messages[-1] if bot.messages else None


async def main():
    SLIP_AMOUNTS.update({"QR_100": 100.0, "QR_300": 300.0, "QR_400": 400.0, "QR_50": 50.0})

    print("=== อัลบั้ม 2 รูป: 100 + 300 = 400 ตรงกับแชท ===")
    msg = await run_group(["QR_100", "QR_300"], CAPTION_400, photo_count=2, msg_id=100)
    check("ต้องขึ้นปุ่มถาม ไม่ auto receive", "Manual review required" in msg["text"], True)
    check("  มีปุ่มให้กด", msg["keyboard"] is not None, True)
    check("  บวกยอดได้ 400.00 THB", "400.00 THB" in msg["text"], True)
    check("  ข้อยอดเงินอยู่ในกลุ่มที่ผ่าน", "Amount 400.00 THB" in msg["text"], True)
    check("  บอกว่ามาจาก 2 รูป", "2 slips in 2 photos" in msg["text"], True)
    check("  ไม่มี backtick โผล่ในข้อความ", "`" in msg["text"], False)
    with SessionLocal() as db:
        check("  บันทึกเป็นรายการเดียว", db.query(Transaction).count(), 1)
        check("  ผูก QR ครบ 2 ใบ", db.query(UsedQR).count(), 2)
        txn = db.query(Transaction).first()
        check("  เก็บยอดรวมลง DB", txn.api_total_amount, 400.0)
        check("  ทุกใบเข้าบัญชีเดียวกัน -> เก็บบัญชีเดียว", txn.receiver_account, "KKP-LS")
        check("  กด Receive แล้วไม่ต้องถาม bank ซ้ำ", h.resolve_known_bank_value(txn), "KKP-LS")
        check("  ชื่อผู้รับที่ซ้ำกันถูกยุบเหลือค่าเดียว", txn.receiver_names, "KKP-LS")
        check("  ชื่อผู้ส่งที่ซ้ำกันถูกยุบเหลือค่าเดียว", txn.sender_names, "นาย ดุลยฤทธิ์ ส")

    print("\n=== รูปเดียว 1 QR ผ่านครบ 3 ข้อ ===")
    msg = await run_group(["QR_400"], CAPTION_400, photo_count=1, msg_id=101)
    check("auto receive", "Received automatically" in msg["text"], True)
    check("  ยอด 400.00 THB", "400.00 THB" in msg["text"], True)

    print("\n=== 1 รูปมีหลาย QR: 100 + 300 ===")
    msg = await run_group(["QR_100", "QR_300"], CAPTION_400, photo_count=1, msg_id=102)
    check("ซ้ำกับใบเดิม -> Duplicate", "Duplicate" in msg["text"], True)

    print("\n=== 1 รูปมีหลาย QR (สลิปคนละใบ): 300 + 100 ===")
    SLIP_AMOUNTS["QR_300B"] = 300.0
    msg = await run_group(["QR_300B", "QR_50"], "TRANS ID : 1\nFULL NAME : ดุลยฤทธิ์\nAMOUNT THB : 350.00",
                          photo_count=1, msg_id=103)
    check("หลาย QR ในรูปเดียว -> ขึ้นปุ่มถาม", "Manual review required" in msg["text"], True)
    check("  บวกได้ 350.00 THB", "350.00 THB" in msg["text"], True)
    check("  บอกว่ามาจาก 1 รูป", "2 slips in 1 photo" in msg["text"], True)

    print("\n=== อัลบั้มที่ยอดรวมไม่ตรงกับแชท ===")
    SLIP_AMOUNTS.update({"QR_A1": 100.0, "QR_A2": 100.0})
    msg = await run_group(["QR_A1", "QR_A2"], CAPTION_400, photo_count=2, msg_id=104)
    check("ยอดไม่ตรงก็ยังถาม ไม่ auto reject", "Manual review required" in msg["text"], True)
    check("  ข้อยอดเงินขึ้นเป็นข้อที่ติด", "❌ Amount does not match" in msg["text"], True)
    check("    เทียบให้เห็นทั้งสองฝั่ง",
          "Slip: 200.00 THB" in msg["text"] and "Chat: 400.00 THB" in msg["text"], True)
    check("  แสดงยอดรวมที่ได้จริง 200.00 THB", "200.00 THB" in msg["text"], True)

    print("\n=== สลิปใบเดียวกันถูกใส่มาซ้ำในอัลบั้มเดียว ===")
    SLIP_AMOUNTS["QR_DUP"] = 100.0
    msg = await run_group(["QR_DUP", "QR_DUP"], "TRANS ID : 2\nFULL NAME : ดุลยฤทธิ์\nAMOUNT THB : 100.00",
                          photo_count=2, msg_id=105)
    check("นับยอดแค่ครั้งเดียว ไม่เบิ้ล", "Manual review required" in msg["text"], True)
    check("  ยอดรวม = 100.00 ไม่ใช่ 200.00", "Amount 100.00 THB" in msg["text"], True)
    with SessionLocal() as db:
        check("  ผูก QR ใบเดียว", db.query(UsedQR).filter(UsedQR.qr_ref == "QR_DUP").count(), 1)

    print("\n=== อัลบั้มที่ API ตรวจไม่ผ่านบางใบ ===")
    SLIP_AMOUNTS["QR_OK"] = 400.0
    msg = await run_group(["QR_OK", "QR_UNKNOWN"], CAPTION_400, photo_count=2, msg_id=106)
    check("ขึ้นปุ่มถามพร้อมเหตุผลจาก API", "Slip not found in bank records." in msg["text"], True)
    check("  ยังเป็น manual review", "Manual review required" in msg["text"], True)

    print("\n=== อัลบั้มที่สลิปเข้าคนละบัญชี ===")
    SLIP_AMOUNTS.update({"QR_B1": 200.0, "QR_B2": 200.0})
    SLIP_RECEIVERS.update({"QR_B1": "KKP-LS", "QR_B2": "SCB-CP"})
    await run_group(["QR_B1", "QR_B2"], CAPTION_400, photo_count=2, msg_id=110)
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.msg_id == "110").first()
        check("บัญชีไม่ตรงกัน -> เก็บทั้งคู่ไว้", txn.receiver_account, "KKP-LS, SCB-CP")
        check("  กด Receive แล้วต้องถามให้เลือกเอง", h.resolve_known_bank_value(txn), "")

    print("\n=== ส่งรูปเดิมซ้ำ (QR ที่เคยใช้แล้ว) ===")
    msg = await run_group(["QR_400"], CAPTION_400, photo_count=1, msg_id=107)
    check("แจ้งว่าซ้ำ", "Duplicate" in msg["text"], True)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
