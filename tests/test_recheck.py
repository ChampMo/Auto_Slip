"""ทดสอบ /recheck และการกันปฏิเสธผิดเมื่ออ่านยอดในข้อความไม่ออก"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "reopen.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111"

for m in ["telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
          "google", "google.oauth2", "google.oauth2.service_account", "googleapiclient",
          "googleapiclient.discovery", "googleapiclient.errors", "requests",
          "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image"]:
    sys.modules.setdefault(m, MagicMock(name=m))
ge = types.ModuleType("gspread.exceptions")
ge.WorksheetNotFound = type("WorksheetNotFound", (Exception,), {})
ge.SpreadsheetNotFound = type("SpreadsheetNotFound", (Exception,), {})
sys.modules["gspread.exceptions"] = ge
sys.modules["telegram.error"].TimedOut = type("TimedOut", (Exception,), {})
sys.modules["telegram.error"].NetworkError = type("NetworkError", (Exception,), {})

import bot.commands as cmds  # noqa: E402
import bot.handlers as h  # noqa: E402
from core.captions import extract_data_from_caption  # noqa: E402
from bot.verification_flow import VerificationDecision, determine_verification_action  # noqa: E402
from database.crud import get_audit_trail, is_sheet_locked  # noqa: E402
from database.models import Transaction  # noqa: E402
from database.session import init_db, SessionLocal  # noqa: E402

init_db()
failed = 0
CHAT = "-100111"


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeUser:
    def __init__(self, uid, name):
        self.id = uid
        self.username = name
        self.full_name = name


ADMIN = FakeUser(111, "adminA")
OUTSIDER = FakeUser(999, "stranger")


class FakeMessage:
    def __init__(self, chat_id=CHAT, msg_id=1, reply_to_id=None):
        self.chat_id = chat_id
        self.message_id = msg_id
        self.reply_to_message = SimpleNamespace(message_id=reply_to_id) if reply_to_id else None
        self.replies = []

    async def reply_text(self, text, reply_markup=None, **kw):
        sent = FakeMessage(self.chat_id, msg_id=9000 + len(self.replies))
        sent.text = text
        sent.keyboard = reply_markup
        self.replies.append(sent)
        return sent

    @property
    def last(self):
        return self.replies[-1] if self.replies else None


async def run_recheck(actor, reply_to_id=None, args=None):
    message = FakeMessage(reply_to_id=reply_to_id)
    await cmds.recheck_command(
        SimpleNamespace(effective_message=message, effective_user=actor),
        SimpleNamespace(args=args or []),
    )
    return message


def make_slip(batch_id, msg_id, status, **kw):
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.add(Transaction(batch_id=batch_id, category="VIP_WE", status=status,
                           chat_id=CHAT, msg_id=str(msg_id), **kw))
        db.commit()


def slip(batch_id):
    with SessionLocal() as db:
        return db.query(Transaction).filter(Transaction.batch_id == batch_id).first()


def text_of(msg):
    return getattr(msg, "text", "") if msg is not None else ""


print("=== 1. อ่าน format ใหม่ที่เจอในกลุ่ม ===")
REAL = ("Hi team, User :  0922972222\n"
        "Amount : THB .- 900\n"
        "KKP LEASON\n"
        "kindly check member's deposit\n"
        "Done inform member about minimum deposit thanks. -maryann")
data = extract_data_from_caption(REAL)
check("อ่านยอดได้แล้ว", data["amount"], 900.0)
check("  ได้ user id", data["user_id"], "0922972222")
check("  ไม่ติดธงเตือน", data["amount_note"], None)

print("\n=== 1b. ไม่มีป้ายเลย แต่มีเลขลอยตรงกับยอดในสลิป ===")
# เคสจริง: "gta6969 / Kritsadakorn Chittiphan / 200" — เลข 200 คือยอด แต่ไม่มีป้ายยืนยัน
from core.captions import find_standalone_numbers  # noqa: E402

REAL_NO_LABEL = ("gta6969\nKritsadakorn Chittiphan\n200\n\n"
                 "Done inform member about minimum deposit thanks\n-joyce")
numbers = find_standalone_numbers(REAL_NO_LABEL)
check("เจอเลข 200", 200.0 in numbers, True)
check("  ไม่หยิบเลขจากชื่อผู้ใช้ gta6969", 6969.0 in numbers, False)
check("  ไม่หยิบเลขจาก aree12b", 12.0 in find_standalone_numbers("aree12b\n5,000"), False)
check("  เจอ 5,000 ที่มีคอมมา", 5000.0 in find_standalone_numbers("aree12b\n5,000"), True)
check("  ข้อความไม่มีตัวเลข", find_standalone_numbers("ช่วยเช็คให้หน่อย"), [])
check("  caption ปกติยังอ่านยอดจากป้ายเหมือนเดิม",
      extract_data_from_caption("User: benz4455\nAmount: 400")["amount"], 400.0)

print("\n=== 2. มีป้ายยอดแต่อ่านตัวเลขไม่ออก -> ห้ามปฏิเสธอัตโนมัติ ===")
broken = extract_data_from_caption("User: abc123\nAmount : THB ???")
check("ยอดยังว่าง", broken["amount"], None)
check("  แต่ติดธงไว้", broken["amount_note"] is not None, True)
check("  โชว์บรรทัดที่อ่านไม่ออก", 'Amount : THB ???' in broken["amount_note"], True)
check("  ธงนี้บังคับให้คนตัดสิน",
      determine_verification_action(True, False, True, True, caption_unreliable=True),
      VerificationDecision.MANUAL_REVIEW)

# เคสจริงที่เคยพลาด: บัญชีผู้รับเป็นของคนอื่น แต่ข้อความไม่มีป้ายบอกยอด
# ธงเรื่องข้อความต้องไม่บังการปฏิเสธเรื่องบัญชี ไม่งั้นเงินที่ไม่ได้เข้าเราจะหลุดไปให้คนกด
check("  บัญชีไม่ใช่ของเรา -> ปฏิเสธเสมอ แม้ข้อความจะน่าสงสัย",
      determine_verification_action(True, False, False, False,
                                    caption_unreliable=True, bank_resolved=True),
      VerificationDecision.AUTO_REJECT)
check("  ถ้าไม่มีธง ยอดไม่ตรงจะโดนปฏิเสธ (พฤติกรรมเดิม)",
      determine_verification_action(True, False, True, True, caption_unreliable=False),
      VerificationDecision.AUTO_REJECT)

no_label = extract_data_from_caption("please check this deposit")
check("ไม่มีป้ายเลย -> ไม่ติดธง (ปฏิเสธได้ตามเดิม)", no_label["amount_note"], None)


async def main():
    print("\n=== 3. /recheck ส่งสลิปที่ถูกปฏิเสธกลับมาตรวจ ===")
    make_slip("r1", 500, "Reject", chat_trans_id="0000123", chat_amount=None,
              receiver_account="KKP-LS")
    msg = await run_recheck(ADMIN, reply_to_id=500)
    check("บอกว่าเปิดใหม่แล้ว", "♻️ Sent back for checking" in text_of(msg.last), True)
    check("  มีปุ่มให้กด", msg.last.keyboard is not None, True)
    check("  บอกว่าใครเปิด", "@adminA" in text_of(msg.last), True)
    check("  สถานะกลับเป็นรอตัดสิน", slip("r1").status, "pending")

    with SessionLocal() as db:
        actions = [a.action for a in get_audit_trail(db, "r1")]
        check("  บันทึก audit ว่าถูกเปิดใหม่", "sent_back_for_checking" in actions, True)
        check("  ปลดล็อกการเขียนชีท", is_sheet_locked(db, "r1"), False)

    print("\n=== 3b. /recheck ต้องโชว์ผลเทียบ ไม่ใช่แค่ค่าที่เก็บไว้ ===")
    make_slip("r1b", 510, "Reject", chat_trans_id="0000200",
              chat_fullname=None, chat_amount=None,
              sender_names="นาย กฤษฎากร ช", api_total_amount=200.0,
              receiver_account="KKP-LS",
              caption_warning="The message has no amount label, but it contains 200.00.")
    msg = await run_recheck(ADMIN, reply_to_id=510)
    body = text_of(msg.last)
    check("เทียบชื่อให้เห็นทั้งสองฝั่ง",
          "Slip: นาย กฤษฎากร ช" in body and "Chat: (not given)" in body, True)
    check("  บอกว่ายอดไม่ตรง", "❌ Amount does not match" in body, True)
    check("  บอกว่าบัญชีผ่าน", "✅ Account KKP-LS" in body, True)
    check("  ยกคำเตือนเดิมมาด้วย", "no amount label" in body, True)
    check("  บอกว่าไม่ได้ตรวจกับธนาคารซ้ำ", "not verified with the bank again" in body, True)

    print("\n=== 4. เปิดด้วย ID ก็ได้ ===")
    make_slip("r2", 501, "Reject", chat_trans_id="0000124")
    msg = await run_recheck(ADMIN, args=["0000124"])
    check("เปิดได้", "♻️ Sent back for checking" in text_of(msg.last), True)
    check("  สถานะเปลี่ยน", slip("r2").status, "pending")

    print("\n=== 5. ใบที่บันทึกลงชีทแล้ว ห้ามเปิดซ้ำ ===")
    make_slip("r3", 502, "Receive", chat_trans_id="0000125", chat_amount=400.0)
    msg = await run_recheck(ADMIN, reply_to_id=502)
    check("ปฏิเสธการเปิด", "already saved to the sheet" in text_of(msg.last), True)
    check("  เตือนว่าจะเขียนซ้ำ", "second row" in text_of(msg.last), True)
    check("  สถานะไม่ถูกแตะ", slip("r3").status, "Receive")

    print("\n=== 6. ใบที่ยังรออยู่ ไม่ต้องเปิด ===")
    make_slip("r4", 503, "pending", chat_trans_id="0000126")
    msg = await run_recheck(ADMIN, reply_to_id=503)
    check("บอกว่ายังไม่จบ", "not finished yet" in text_of(msg.last), True)
    check("  สถานะไม่เปลี่ยน", slip("r4").status, "pending")

    print("\n=== 7. ใบที่ค้างจากบอทดับ เปิดได้ ===")
    make_slip("r5", 504, "interrupted", chat_trans_id="0000127")
    msg = await run_recheck(ADMIN, reply_to_id=504)
    check("เปิดได้", "♻️ Sent back for checking" in text_of(msg.last), True)
    check("  สถานะกลับเป็นรอตัดสิน", slip("r5").status, "pending")

    print("\n=== 8. สิทธิ์และการใช้ผิดวิธี ===")
    make_slip("r6", 505, "Reject", chat_trans_id="0000128")
    msg = await run_recheck(OUTSIDER, reply_to_id=505)
    check("คนนอกใช้ไม่ได้", "Only approvers can send a slip back" in text_of(msg.last), True)
    check("  สถานะไม่ถูกแตะ", slip("r6").status, "Reject")

    msg = await run_recheck(ADMIN)
    check("ไม่ reply ไม่ใส่ ID -> แนะนำวิธี", "Reply to the slip photo" in text_of(msg.last), True)
    msg = await run_recheck(ADMIN, reply_to_id=99999)
    check("  reply ผิดใบ", "No slip is linked" in text_of(msg.last), True)
    msg = await run_recheck(ADMIN, args=["ไม่มีจริง"])
    check("  ID ไม่มีจริง", "No slip found for ID" in text_of(msg.last), True)

    print("\n=== 9. เปิดแล้วกด Receive ได้จริง ===")
    check("ปุ่มผูกกับ batch เดิม", slip("r1").status, "pending")
    with SessionLocal() as db:
        check("  ชีทไม่ถูกล็อก จึงกดบันทึกได้", is_sheet_locked(db, "r1"), False)


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
