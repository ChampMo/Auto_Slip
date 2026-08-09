"""ทดสอบ flow ให้แอดมินพิมพ์ยอดเอง ตอนสลิปไม่มียอดให้ใช้เลย"""
import asyncio
import os
import sys
import tempfile
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

DB_PATH = os.path.join(tempfile.mkdtemp(), "amount.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"
os.environ["SLIP_APPROVER_IDS"] = "111,222"

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
from database.models import Transaction, AuditLog  # noqa: E402

init_db()

failed = 0
sheet_writes = []


def fake_append_to_sheet(entry):
    sheet_writes.append({
        "batch_id": entry.batch_id,
        "amount": entry.api_total_amount if entry.api_total_amount is not None else entry.chat_amount,
        "bank": entry.receiver_account,
    })
    return True, ""


h.append_to_sheet = fake_append_to_sheet


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


def text_of(message) -> str:
    """ข้อความที่บอทตอบ — ถ้าไม่ได้ตอบเลยให้เป็นค่าว่าง จะได้เห็นว่า FAIL ตรงไหน
    แทนที่จะพังทั้งไฟล์"""
    return getattr(message, "text", "") if message is not None else ""


class FakeUser:
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username
        self.full_name = username


ADMIN_A = FakeUser(111, "adminA")
ADMIN_B = FakeUser(222, "adminB")

CHAT = "-100111"
_next_msg_id = [900]


class FakeMessage:
    """แทนข้อความในกลุ่ม จำได้ว่าตอบกลับอะไรไปบ้าง"""

    def __init__(self, chat_id=CHAT, msg_id=None, text="", user=ADMIN_A, reply_to=None):
        _next_msg_id[0] += 1
        self.chat_id = chat_id
        self.message_id = msg_id if msg_id is not None else _next_msg_id[0]
        self.text = text
        self.from_user = user
        self.reply_to_message = reply_to
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        sent = FakeMessage(self.chat_id, text=text)
        sent.keyboard = reply_markup
        self.replies.append(sent)
        return sent

    @property
    def last(self):
        return self.replies[-1] if self.replies else None


class FakeQuery:
    def __init__(self, data, user=ADMIN_A, message=None):
        self.data = data
        self.from_user = user
        self.message = message or FakeMessage()
        self.alerts = []
        self.texts = []
        self.markups = []

    async def answer(self, text=None, show_alert=False):
        if text:
            self.alerts.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.texts.append(text)
        self.markups.append(reply_markup)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markups.append(reply_markup)

    @property
    def last_text(self):
        return self.texts[-1] if self.texts else ""


class FakeGroupBot:
    """จำว่าบอทไปสั่งปิดปุ่มบนข้อความไหนบ้าง"""

    def __init__(self):
        self.closed = []

    async def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self.closed.append({"msg_id": message_id, "markup": reply_markup})


GROUP_BOT = FakeGroupBot()


async def press(callback_data, user=ADMIN_A, message=None):
    query = FakeQuery(callback_data, user, message)
    await h.button_callback(
        SimpleNamespace(callback_query=query), SimpleNamespace(bot=GROUP_BOT)
    )
    return query


async def reply_with(text, to_message, user=ADMIN_A):
    message = FakeMessage(to_message.chat_id, text=text, user=user, reply_to=to_message)
    await h.handle_amount_reply(
        SimpleNamespace(message=message), SimpleNamespace(bot=MagicMock())
    )
    return message


def new_slip(batch_id, chat_amount=None, api_amount=None, receiver_account=None,
             status="pending", review_msg_id="555", transfer_time_text="0:18"):
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status=status,
            chat_id=CHAT, msg_id="1", chat_trans_id="0001",
            chat_amount=chat_amount, api_total_amount=api_amount,
            receiver_account=receiver_account, review_msg_id=review_msg_id,
            transfer_time_text=transfer_time_text,
        ))
        db.commit()


def slip(batch_id):
    with SessionLocal() as db:
        return db.query(Transaction).filter(Transaction.batch_id == batch_id).first()


def actions_of(batch_id):
    with SessionLocal() as db:
        return [a.action for a in db.query(AuditLog).filter(AuditLog.qr_ref == batch_id)]


async def main():
    print("=== กด Receive ตอนไม่รู้ยอด -> ขอให้พิมพ์ยอดมา ===")
    new_slip("amt1", transfer_time_text=None)
    query = await press("receive_amt1")
    question = query.message.last
    check("ถามหายอด", "💬 Amount needed" in text_of(question), True)
    check("  บอกวิธีตอบ", "Reply to this message with the amount" in text_of(question), True)
    check("  ยังไม่ขึ้นปุ่มเลือกธนาคาร", getattr(question, "keyboard", None), None)
    check("  ยังไม่ตัดสินอะไร สถานะยังรออยู่", slip("amt1").status, "pending")
    check("  ยังไม่เขียนลงชีท", sheet_writes, [])

    print("\n=== ตอบยอดกลับมา -> ไปต่อที่เลือกธนาคาร ===")
    answer = await reply_with("1500", question)
    check("ยืนยันยอดที่ตั้ง", "Amount set to 1500.00 THB" in text_of(answer.replies[0]), True)
    check("  ยอดถูกบันทึกไว้แล้ว", slip("amt1").chat_amount, 1500.0)
    check("  audit บันทึกว่าใครใส่ยอด", "amount_entered_manually" in actions_of("amt1"), True)
    check("  สถานะยังไม่ถูกตัดสิน", slip("amt1").status, "pending")
    # สลิปใบนี้อ่านอะไรไม่ได้เลย เวลาโอนจึงไม่รู้ด้วย ต้องถามต่อก่อนไปเลือกธนาคาร
    check("  ถามเวลาโอนต่อ", "🕒 Transfer time needed" in text_of(answer.last), True)
    check("  ยังไม่ขึ้นปุ่มธนาคาร", getattr(answer.last, "keyboard", None), None)

    print("\n=== ตอบเวลาโอนกลับมา -> ค่อยไปเลือกธนาคาร ===")
    # พิมพ์มาแค่เวลาไม่พอ ต้องมีวันด้วย ไม่งั้น transfer_at จะว่างแล้วด่านเตือนซ้ำจะเงียบ
    reject_time = await reply_with("0:18", answer.last)
    check("พิมพ์มาแค่เวลา -> ไม่รับ",
          "not a valid date and time" in text_of(reject_time.last), True)
    check("  ยังไม่บันทึกเวลา", slip("amt1").transfer_time_text, None)

    time_answer = await reply_with("8/8/69 0:18", answer.last)
    check("ยืนยันวันและเวลา", "Transfer time set to 08/08/2026 0:18" in text_of(time_answer.last), True)
    # ต้องมีวันเสมอ ไม่งั้น transfer_at จะว่าง แล้วด่านเตือนซ้ำระดับนาทีจะเงียบ
    check("  เก็บ transfer_at เป็น datetime เต็ม", slip("amt1").transfer_at is not None, True)
    check("  ขอให้เลือกธนาคารต่อ", "choose the bank account" in text_of(time_answer.last), True)
    check("  มีปุ่มธนาคารให้กด", time_answer.last.keyboard is not None, True)
    check("  เวลาถูกบันทึกไว้แล้ว", slip("amt1").transfer_time_text, "0:18")
    check("  audit บันทึกว่าใครใส่เวลา",
          "transfer_time_entered_manually" in actions_of("amt1"), True)
    check("  สถานะยังไม่ถูกตัดสิน", slip("amt1").status, "pending")

    print("\n=== เลือกธนาคารแล้ว -> ยอดที่พิมพ์ต้องลงชีท ===")
    GROUP_BOT.closed.clear()
    await press("bank_amt1_KKP-LS")
    check("เขียนลงชีท 1 แถว", len(sheet_writes), 1)
    check("  ใช้ยอดที่แอดมินพิมพ์", sheet_writes[0]["amount"], 1500.0)
    check("  ใช้ธนาคารที่เลือก", sheet_writes[0]["bank"], "KKP-LS")
    check("  สถานะจบเป็น Receive", slip("amt1").status, "Receive")

    print("\n=== จบแล้ว ปุ่มบนข้อความตรวจสอบใบเดิมต้องหายไป ===")
    first_close = GROUP_BOT.closed[0] if GROUP_BOT.closed else {}
    check("สั่งปิดปุ่ม 1 ครั้ง", len(GROUP_BOT.closed), 1)
    check("  ปิดที่ข้อความตรวจสอบใบเดิม", first_close.get("msg_id"), 555)
    check("  ปิดจริง (ไม่ใช่เปลี่ยนปุ่ม)", first_close.get("markup"), None)

    print("\n=== กด Receive อีกครั้ง ตอนยอดรู้แล้ว -> ไม่ถามซ้ำ ===")
    new_slip("amt2", chat_amount=800.0)
    query = await press("receive_amt2")
    check("ไม่ถามหายอด", query.message.replies, [])
    check("  ข้ามไปเลือกธนาคารเลย", len(query.markups), 1)

    print("\n=== รู้ยอดจากสลิป (ไม่มีในแชท) ก็ไม่ต้องถาม ===")
    new_slip("amt3", api_amount=250.0)
    query = await press("receive_amt3")
    check("ไม่ถามหายอด", query.message.replies, [])

    print("\n=== คนอื่นตอบแทนไม่ได้ ===")
    new_slip("amt4")
    query = await press("receive_amt4", user=ADMIN_A)
    question4 = query.message.last
    other = await reply_with("999", question4, user=ADMIN_B)
    check("ปฏิเสธคนที่ไม่ได้ถูกถาม", "requested from someone else" in text_of(other.last), True)
    check("  ยอดไม่ถูกเปลี่ยน", slip("amt4").chat_amount, None)
    mine = await reply_with("999", question4, user=ADMIN_A)
    check("  คนที่ถูกถามยังตอบได้อยู่", slip("amt4").chat_amount, 999.0)

    print("\n=== พิมพ์มาไม่ใช่ยอด -> ให้พิมพ์ใหม่ได้ ===")
    new_slip("amt5")
    query = await press("receive_amt5")
    question5 = query.message.last
    bad = await reply_with("ไม่รู้", question5)
    check("บอกว่าใช้ไม่ได้", "not a valid amount" in text_of(bad.last), True)
    check("  ยอดยังว่าง", slip("amt5").chat_amount, None)
    zero = await reply_with("0", question5)
    check("  ศูนย์ก็ใช้ไม่ได้", "not a valid amount" in text_of(zero.last), True)
    good = await reply_with("2,500.50", question5)
    check("  พิมพ์ใหม่ให้ถูกแล้วผ่าน", slip("amt5").chat_amount, 2500.50)
    check("  ไปต่อที่เลือกธนาคาร", good.last.keyboard is not None, True)

    print("\n=== ตอบซ้ำหลังใช้ไปแล้ว -> เงียบ ===")
    again = await reply_with("7777", question5)
    check("ไม่ตอบอะไร", again.replies, [])
    check("  ยอดไม่ถูกเขียนทับ", slip("amt5").chat_amount, 2500.50)

    print("\n=== ตอบข้อความอื่นที่ไม่ใช่คำถามของบอท -> ไม่ยุ่ง ===")
    unrelated = FakeMessage(CHAT, text="1200")
    noise = await reply_with("1200", unrelated)
    check("ไม่ตอบอะไร", noise.replies, [])

    print("\n=== สลิปถูกตัดสินไปแล้วระหว่างรอคำตอบ ===")
    new_slip("amt6")
    query = await press("receive_amt6")
    question6 = query.message.last
    await press("reject_amt6", user=ADMIN_B)
    late = await reply_with("500", question6)
    check("บอกว่าตัดสินไปแล้ว", "already rejected" in text_of(late.last), True)
    check("  ยอดไม่ถูกเปลี่ยน", slip("amt6").chat_amount, None)
    check("  สถานะยังเป็น Reject", slip("amt6").status, "Reject")

    print("\n=== คำขอหมดอายุ -> เมิน ===")
    new_slip("amt7")
    query = await press("receive_amt7")
    question7 = query.message.last
    key = h._amount_request_key(question7.chat_id, question7.message_id)
    h._amount_requests[key]["expires_at"] = 0.0
    expired = await reply_with("400", question7)
    check("ไม่ตอบอะไร", expired.replies, [])
    check("  ยอดไม่ถูกตั้ง", slip("amt7").chat_amount, None)

    print("\n=== กด Reject ก็ต้องปิดปุ่มใบเดิมด้วย ===")
    new_slip("amt9", chat_amount=100.0, review_msg_id="777")
    GROUP_BOT.closed.clear()
    await press("reject_amt9")
    check("สั่งปิดปุ่ม", [c["msg_id"] for c in GROUP_BOT.closed], [777])
    check("  สถานะเป็น Reject", slip("amt9").status, "Reject")

    print("\n=== กดบนข้อความตรวจสอบเอง -> ไม่ต้องสั่งปิดซ้ำ ===")
    new_slip("amt10", chat_amount=100.0, receiver_account="KKP-LS", review_msg_id="888")
    GROUP_BOT.closed.clear()
    review_message = FakeMessage(CHAT, msg_id=888)
    writes_before = len(sheet_writes)
    await press("receive_amt10", message=review_message)
    check("ไม่สั่งปิดซ้ำ (ข้อความเดียวกับที่แก้ไปแล้ว)", GROUP_BOT.closed, [])
    check("  ยังบันทึกลงชีทตามปกติ", len(sheet_writes) - writes_before, 1)

    print("\n=== ไม่รู้ว่าปุ่มอยู่ข้อความไหน -> ข้ามไป ไม่พัง ===")
    new_slip("amt11", chat_amount=100.0, receiver_account="KKP-LS", review_msg_id=None)
    GROUP_BOT.closed.clear()
    await press("receive_amt11")
    check("ไม่สั่งปิดอะไร", GROUP_BOT.closed, [])
    check("  ยังทำงานจบปกติ", slip("amt11").status, "Receive")

    print("\n=== คนไม่มีสิทธิ์กด Receive ไม่ได้ตั้งแต่แรก ===")
    new_slip("amt8")
    outsider = FakeUser(999, "stranger")
    query = await press("receive_amt8", user=outsider)
    check("ถูกปฏิเสธ", any("not allowed" in alert for alert in query.alerts), True)
    check("  ไม่มีคำถามถูกส่ง", query.message.replies, [])


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
