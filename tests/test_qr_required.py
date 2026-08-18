"""ทุกใบต้องมี QR — และใบที่ QR เสียจริงต้องกันซ้ำด้วยแฮชของไฟล์รูป"""
import os
import sys
import tempfile
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "qr.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"

for module_name in [
    "telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
    "google", "google.oauth2", "google.oauth2.service_account",
    "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
    "requests", "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(module_name, MagicMock(name=module_name))

from core.matcher import NEEDS_QR_STATUS, photo_key, process_incoming_slip  # noqa: E402
from core.scanner import file_sha256  # noqa: E402
from database.models import Transaction, UsedQR  # noqa: E402
from database.session import SessionLocal, init_db  # noqa: E402

init_db()
CHAT = "-100111"
CAPTION = "TRANS ID : 0000123\nFULL NAME : ดุลยฤทธิ์ สมบูรณ์\nAMOUNT THB : 400.00"

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def run(qr_list, msg_id, photo_hash, allow_without_qr=False, force_batch_id=None):
    with SessionLocal() as db:
        status, txn = process_incoming_slip(
            db, qr_list, CHAT, str(msg_id), CAPTION,
            photo_hashes=[photo_hash], allow_without_qr=allow_without_qr,
            force_batch_id=force_batch_id,
        )
        return status, (txn.batch_id if txn else None), (txn.status if txn else None)


print("=== อ่าน QR ไม่ออก -> ต้องขอ QR ก่อน ===")
status, batch_a, txn_status = run([], 100, "hashA")
check("ยังไม่รับเข้าระบบ", status, "needs_qr")
check("  สถานะรอ QR", txn_status, NEEDS_QR_STATUS)
with SessionLocal() as db:
    check("  ยังไม่จองแฮชรูป (ส่งรูปเดิมมาใหม่ได้)",
          db.query(UsedQR).filter(UsedQR.qr_ref == photo_key("hashA")).count(), 0)
    check("  เก็บแฮชไว้ในแถวแล้ว",
          db.query(Transaction).filter(Transaction.batch_id == batch_a).first().photo_hash, "hashA")

print("\n=== ส่งรูปเดิมซ้ำระหว่างรอ QR -> ทวงใหม่ ไม่ตีเป็นของซ้ำ ===")
# เกิดตอนบอทรีสตาร์ทแล้วคนส่งรูปเดิมเข้ามาใหม่ ถ้าตีเป็นซ้ำจะไปต่อไม่ได้เลย
check("ทวงซ้ำได้", run([], 100, "hashA")[0], "needs_qr")

print("\n=== ส่ง QR ตามมา -> รับเข้าระบบ ===")
status, batch_b, txn_status = run(["EMV_PAYLOAD_0001"], 100, "hashA",
                                  force_batch_id=batch_a)
check("รับเข้าระบบ", status, "new")
check("  สถานะรอคนตัดสิน", txn_status, "pending")
# เติม QR คือเอาชุดเดิมมาตรวจใหม่ ไม่ใช่การส่งสลิปซ้ำ รหัสชุดจึงต้องคงเดิม
check("  คงรหัสชุดเดิม", batch_b, batch_a)
with SessionLocal() as db:
    check("  จอง QR แล้ว",
          db.query(UsedQR).filter(UsedQR.qr_ref == "EMV_PAYLOAD_0001").count(), 1)
    check("  จองแฮชรูปแล้ว",
          db.query(UsedQR).filter(UsedQR.qr_ref == photo_key("hashA")).count(), 1)

print("\n=== QR เดิมส่งมาอีกครั้งจากรูปใหม่ -> ซ้ำ ===")
check("จับซ้ำจาก QR", run(["EMV_PAYLOAD_0001"], 101, "hashB")[0], "duplicate")

print("\n=== QR เสียจริง -> รับได้แต่ต้องกันซ้ำด้วยแฮชรูป ===")
status, batch_c, txn_status = run([], 200, "hashC", allow_without_qr=True)
check("รับเข้าระบบ", status, "new")
check("  สถานะรอคนตัดสิน", txn_status, "pending")
with SessionLocal() as db:
    check("  จองแฮชรูปแล้ว",
          db.query(UsedQR).filter(UsedQR.qr_ref == photo_key("hashC")).count(), 1)

print("\n=== ไฟล์เดิมเป๊ะถูกส่งซ้ำ -> ซ้ำ แม้ไม่มี QR เลย ===")
check("จับซ้ำจากแฮชรูป", run([], 201, "hashC", allow_without_qr=True)[0], "duplicate")
check("  ไฟล์คนละไฟล์ยังผ่านได้", run([], 202, "hashD", allow_without_qr=True)[0], "new")

print("\n=== ปุ่ม Duplicate ===")
from bot.keyboards import get_approval_keyboard  # noqa: E402
from database.crud import DECIDED_STATUSES  # noqa: E402
from core.matcher import REJECTED_STATUSES  # noqa: E402

def button_data(build):
    """ดูว่าสร้างปุ่มอะไรบ้าง — telegram ถูกแทนด้วยของปลอม จึงอ่านจากการเรียกแทน"""
    button = sys.modules["telegram"].InlineKeyboardButton
    button.reset_mock()
    build()
    return [call.kwargs.get("callback_data", "") for call in button.call_args_list]

plain = button_data(lambda: get_approval_keyboard("b1"))
with_dup = button_data(lambda: get_approval_keyboard("b1", with_duplicate=True))
check("ปกติไม่มีปุ่ม Duplicate", any(d.startswith("dup_") for d in plain), False)
check("  สงสัยว่าซ้ำ -> มีปุ่ม", any(d.startswith("dup_") for d in with_dup), True)
check("  ยังมี Receive/Reject ครบ", len(with_dup), 3)
# ปุ่มเติม QR ต้องมีทุกใบที่ให้คนตัดสิน เพราะระบบไม่มีทางรู้เองว่าอ่าน QR ได้ไม่ครบ
# ปุ่มเติม QR ถูกปิดไว้ — ปุ่มค้างทำให้ดูเหมือนงานยังไม่จบ ใช้ /recheck แทน
check("  ไม่มีปุ่มเติม QR แล้ว", any(d.startswith("addqr_") for d in plain), False)
check("  Duplicate ถือว่าตัดสินแล้ว", "Duplicate" in DECIDED_STATUSES, True)
# ถ้าอยู่ใน REJECTED_STATUSES จะปลดล็อกให้ส่งใบเดิมเข้ามาได้อีก ซึ่งผิด
check("  ไม่ปลดล็อกให้ส่งซ้ำ", "duplicate" in REJECTED_STATUSES, False)

print("\n=== ใครตอบคำขอ QR ได้ ===")
import bot.handlers as h  # noqa: E402

# QR ตัดสินว่าเงินยืนยันกับธนาคารได้หรือไม่ คนนอกจึงยัดเข้ามาไม่ได้
h.load_approver_ids = lambda: {"111"}
approver = type("U", (), {"id": 111})()
outsider = type("U", (), {"id": 999})()
check("ผู้อนุมัติตอบได้", h.may_answer_qr_request(approver), True)
check("  คนนอกตอบไม่ได้", h.may_answer_qr_request(outsider), False)
check("  ไม่รู้ว่าใคร ก็ตอบไม่ได้", h.may_answer_qr_request(None), False)

print("\n=== แฮชไฟล์ ===")
tmp = os.path.join(tempfile.mkdtemp(), "a.jpg")
with open(tmp, "wb") as f:
    f.write(b"same-bytes")
same = os.path.join(tempfile.mkdtemp(), "b.jpg")
with open(same, "wb") as f:
    f.write(b"same-bytes")
check("ไฟล์เนื้อเดียวกัน คนละชื่อ -> แฮชเท่ากัน", file_sha256(tmp), file_sha256(same))
check("  ไฟล์หาย -> คืนค่าว่าง ไม่ระเบิด", file_sha256("no-such-file.jpg"), "")

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
