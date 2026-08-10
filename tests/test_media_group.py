"""ทดสอบการรวมรูปหลายใบในข้อความเดียว (อัลบั้ม) ด้วยฟังก์ชันจริงใน bot/handlers.py"""
import asyncio
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ["DATABASE_URL"] = "sqlite://"
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

failed = 0


def check(label, got, expected):
    global failed
    ok = got == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")


processed = []


async def fake_process_slip_group(bot, chat_id, msg_id, caption, qr_list, photo_count,
                                  photo_hashes=None, allow_without_qr=False, **kw):
    processed.append({
        "chat_id": chat_id, "msg_id": msg_id, "caption": caption,
        "qr_list": list(qr_list), "photo_count": photo_count,
    })


h.process_slip_group = fake_process_slip_group
h.MEDIA_GROUP_WAIT_SECONDS = 0.05
BOT = MagicMock(name="bot")


async def send_album(group_id, photos, gap=0.01):
    """photos = [(msg_id, caption, [qr...]), ...]"""
    for msg_id, caption, qr_list in photos:
        await h.collect_media_group_photo(group_id, BOT, "-100111", msg_id, caption, qr_list)
        await asyncio.sleep(gap)


async def main():
    # 1. เคสในรูป: 2 รูปในข้อความเดียว caption ติดมาแค่ใบแรก (100 + 300 = 400)
    processed.clear()
    await send_album("G1", [
        (10, "Hi team, User :  benz4455\nAmount : THB 400\nKKP - LEASON", ["QR_100"]),
        (11, "", ["QR_300"]),
    ])
    await asyncio.sleep(0.3)

    check("อัลบั้ม 2 รูป ประมวลผลครั้งเดียว", len(processed), 1)
    if processed:
        group = processed[0]
        check("  รวม QR ครบทุกใบ", group["qr_list"], ["QR_100", "QR_300"])
        check("  นับจำนวนรูปถูก", group["photo_count"], 2)
        check("  ใช้ caption จากใบที่ติดมา", "THB 400" in group["caption"], True)
        check("  ตอบกลับที่ใบที่มี caption", group["msg_id"], 10)

    # 2. caption ติดมากับใบที่สอง
    processed.clear()
    await send_album("G2", [
        (20, "", ["QR_A"]),
        (21, "TRANS ID : 0009\nFULL NAME : ดุลยฤทธิ์\nAMOUNT THB : 400", ["QR_B"]),
    ])
    await asyncio.sleep(0.3)
    check("caption อยู่ใบที่สอง", processed[0]["msg_id"] if processed else None, 21)
    check("  หยิบ caption มาได้", "0009" in processed[0]["caption"] if processed else False, True)

    # 3. รูปมาช้ากว่ากันเล็กน้อย ต้องยังนับเป็นอัลบั้มเดียว (timer ถูกเลื่อนทุกครั้ง)
    processed.clear()
    await send_album("G3", [
        (30, "Amount : THB 600", ["QR_X"]),
        (31, "", ["QR_Y"]),
        (32, "", ["QR_Z"]),
    ], gap=0.04)
    await asyncio.sleep(0.3)
    check("รูปทยอยมา 3 ใบ ยังเป็นชุดเดียว", len(processed), 1)
    check("  ครบ 3 ใบ", processed[0]["photo_count"] if processed else 0, 3)

    # 4. คนละอัลบั้ม ต้องแยกกัน
    processed.clear()
    await asyncio.gather(
        send_album("G4", [(40, "Amount : THB 100", ["QR_G4"])]),
        send_album("G5", [(50, "Amount : THB 200", ["QR_G5"])]),
    )
    await asyncio.sleep(0.3)
    check("คนละอัลบั้มแยกรายการ", len(processed), 2)
    check("  ไม่มี QR ปนกัน",
          sorted(qr for p in processed for qr in p["qr_list"]), ["QR_G4", "QR_G5"])

    # 5. อัลบั้มที่อ่าน QR ไม่ออกเลย ต้องยังส่งต่อไปให้แอดมินตัดสิน
    processed.clear()
    await send_album("G6", [(60, "Amount : THB 100", []), (61, "", [])])
    await asyncio.sleep(0.3)
    check("อัลบั้มที่อ่าน QR ไม่ได้เลย ยังต้องส่งต่อ", len(processed), 1)
    check("  ส่งไปแบบไม่มี QR", processed[0]["qr_list"] if processed else None, [])
    check("  ยังนับจำนวนรูปครบ", processed[0]["photo_count"] if processed else 0, 2)

    # 6. buffer ต้องถูกล้างทิ้งทุกครั้ง ไม่ค้างสะสม
    check("ไม่มี media group ค้างใน buffer", len(h._media_groups), 0)

    # 7. รูปเดียวปกติ (ไม่มี media_group_id) ต้องประมวลผลทันที ไม่ต้องรอ
    processed.clear()
    h.download_and_scan_photo = lambda message: asyncio.sleep(0, result=(["QR_SINGLE"], "hash_single"))
    message = SimpleNamespace(chat_id="-100111", message_id=70, caption="Amount : THB 100",
                              media_group_id=None, photo=[MagicMock()])
    await h.handle_photo(SimpleNamespace(message=message), SimpleNamespace(bot=BOT))
    check("รูปเดียวประมวลผลทันที", len(processed), 1)
    check("  นับเป็น 1 รูป", processed[0]["photo_count"] if processed else 0, 1)

    # 8. โหลดรูปไม่สำเร็จ (คืน None) ต้องไม่ประมวลผลต่อ
    processed.clear()
    h.download_and_scan_photo = lambda message: asyncio.sleep(0, result=None)
    await h.handle_photo(SimpleNamespace(message=message), SimpleNamespace(bot=BOT))
    check("โหลดรูปไม่สำเร็จ", len(processed), 0)

    # 9. รูปเดียวที่อ่าน QR ไม่ได้ ต้องส่งต่อไปให้แอดมินตัดสิน
    processed.clear()
    h.download_and_scan_photo = lambda message: asyncio.sleep(0, result=([], "hash_none"))
    await h.handle_photo(SimpleNamespace(message=message), SimpleNamespace(bot=BOT))
    check("รูปเดียวอ่าน QR ไม่ออก ยังต้องส่งต่อ", len(processed), 1)
    check("  ส่งไปแบบไม่มี QR", processed[0]["qr_list"] if processed else None, [])


asyncio.run(main())
print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
sys.exit(1 if failed else 0)
