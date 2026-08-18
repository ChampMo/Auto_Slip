"""ตอบกลับข้อความทวง QR ด้วยรูป — เส้นทางที่เคยพังทั้งเส้นโดยไม่มีเทสต์จับ

บั๊กเดิม: ด่านกรอง "รูปไม่มี caption = ไม่ใช่สลิป" อยู่ก่อนการเช็คว่าเป็นคำตอบไหม
คนตอบด้วยรูป QR ที่ครอปมาแทบไม่ใส่ caption รูปจึงถูกทิ้งทุกใบ
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

DB = os.path.join(tempfile.mkdtemp(), "reply.db")
os.environ["DATABASE_URL"] = f"sqlite:///{DB}"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"

for name in [
    "telegram", "telegram.ext", "telegram.error", "gspread", "gspread.exceptions",
    "google", "google.oauth2", "google.oauth2.service_account",
    "googleapiclient", "googleapiclient.discovery", "googleapiclient.errors",
    "requests", "pyzbar", "pyzbar.pyzbar", "PIL", "PIL.Image",
]:
    sys.modules.setdefault(name, MagicMock(name=name))

import bot.handlers as h  # noqa: E402
from core.matcher import NEEDS_QR_STATUS  # noqa: E402
from database.models import Transaction  # noqa: E402
from database.session import SessionLocal, init_db  # noqa: E402

init_db()
CHAT = "-100111"
CAPTION = "TRANS ID : 0000123\nAMOUNT THB : 400.00"
APPROVER = SimpleNamespace(id=111, username="boss", full_name="Boss")
OUTSIDER = SimpleNamespace(id=999, username="rando", full_name="Rando")
h.load_approver_ids = lambda: {"111"}

failed = 0
resumed = []


def check(label, got, expected):
    global failed
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


class FakeMessage:
    def __init__(self, msg_id, caption="", reply_to=None, user=APPROVER):
        self.message_id = msg_id
        self.chat_id = int(CHAT)
        self.caption = caption
        self.text = ""
        self.reply_to_message = reply_to
        self.from_user = user
        self.media_group_id = None
        self.photo = [object()]
        self.replies = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        return SimpleNamespace(message_id=self.message_id + 500)

    @property
    def last(self):
        return self.replies[-1] if self.replies else ""


def make_waiting(batch_id, question_msg_id):
    with SessionLocal() as db:
        db.query(Transaction).filter(Transaction.batch_id == batch_id).delete()
        db.add(Transaction(
            batch_id=batch_id, category="VIP_WE", status=NEEDS_QR_STATUS,
            chat_id=CHAT, msg_id="10", raw_caption=CAPTION, chat_amount=400.0,
            photo_hash="hash1", qr_request_msg_id=str(question_msg_id),
        ))
        db.commit()


async def send(message, qr_list):
    h.download_and_scan_photo = lambda m: asyncio.sleep(0, result=(qr_list, "hash1"))

    async def fake_group(bot, chat_id, msg_id, caption, qr, photo_count,
                         photo_hashes=None, allow_without_qr=False, force_batch_id=None, **kw):
        resumed.append({"caption": caption, "qr": qr, "batch": force_batch_id})

    h.process_slip_group = fake_group
    await h.handle_photo(SimpleNamespace(message=message), SimpleNamespace(bot=MagicMock()))


async def main():
    print("=== ตอบด้วยรูป QR ที่ไม่มี caption (เคสที่เคยพัง) ===")
    make_waiting("b1", 900)
    question = FakeMessage(900)
    reply = FakeMessage(901, caption="", reply_to=question)
    resumed.clear()
    await send(reply, ["EMV_NEW"])
    check("ไม่ถูกทิ้งเพราะไม่มี caption", len(resumed), 1)
    check("  ใช้ caption ของข้อความต้นฉบับ", resumed[0]["caption"], CAPTION)
    check("  คงรหัสชุดเดิม", resumed[0]["batch"], "b1")

    print("\n=== รูปที่อ่าน QR ยังไม่ออก ===")
    make_waiting("b2", 910)
    reply = FakeMessage(911, caption="", reply_to=FakeMessage(910))
    resumed.clear()
    await send(reply, [])
    check("ไม่ไปต่อ", len(resumed), 0)
    check("  บอกให้ครอปใหม่", "Still no QR code" in reply.last, True)

    print("\n=== คนไม่มีสิทธิ์ตอบ ===")
    make_waiting("b3", 920)
    reply = FakeMessage(921, caption="", reply_to=FakeMessage(920), user=OUTSIDER)
    resumed.clear()
    await send(reply, ["EMV_X"])
    check("ไม่ไปต่อ", len(resumed), 0)
    check("  บอกว่าเฉพาะผู้อนุมัติ", "Only approvers" in reply.last, True)

    print("\n=== รูปตอบกลับที่ไม่มีอะไรรออยู่ ===")
    reply = FakeMessage(931, caption="", reply_to=FakeMessage(930))
    resumed.clear()
    await send(reply, ["EMV_Y"])
    check("ทิ้งไป ไม่กลายเป็นสลิปใบใหม่", len(resumed), 0)

    print("\n=== รูปธรรมดาไม่มี caption (ไม่ใช่การตอบกลับ) ===")
    reply = FakeMessage(941, caption="")
    resumed.clear()
    await send(reply, ["EMV_Z"])
    check("ยังถูกกรองทิ้งเหมือนเดิม", len(resumed), 0)

    print("\n=== รูปสลิปปกติที่มี caption ===")
    normal = FakeMessage(951, caption=CAPTION)
    resumed.clear()
    await send(normal, ["EMV_OK"])
    check("เข้ากระบวนการปกติ", len(resumed), 1)
    check("  ไม่ได้บังคับรหัสชุด", resumed[0]["batch"], None)

    print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
    sys.exit(1 if failed else 0)


asyncio.run(main())
