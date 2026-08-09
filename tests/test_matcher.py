"""ทดสอบ flow ของ process_incoming_slip กับ sqlite in-memory"""
import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["VIP_WE_CHAT_ID"] = "-100111"
os.environ["VIP_12_CHAT_ID"] = "-100222"

from database.session import Base, engine, SessionLocal  # noqa: E402
import database.models  # noqa: E402,F401
from database.models import Transaction, UsedQR  # noqa: E402
from core.matcher import process_incoming_slip  # noqa: E402

Base.metadata.create_all(bind=engine)

WE = "-100111"
T12 = "-100222"
OTHER = "-999999"

CAP_A = "Hi team, User :  benz4455\nAmount : THB 400\nKKP - LEASON"
CAP_B = "TRANS ID : 0000004\nFULL NAME : Somchai J\nAMOUNT THB : 2500.00"

failed = 0


def check(label, actual, expected):
    global failed
    ok = actual == expected
    if not ok:
        failed += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={actual!r} expected={expected!r}")


with SessionLocal() as db:
    # 1. กลุ่มที่ไม่ได้ลงทะเบียน
    status, txn = process_incoming_slip(db, ["QR_X"], OTHER, 1, CAP_A)
    check("กลุ่มแปลกปลอมถูกข้าม", status, "unknown_group")

    # 2. VIP_WE ส่ง format A -> รับเข้าระบบทันที (ไม่ต้องรอคู่)
    status, txn = process_incoming_slip(db, ["QR_1"], WE, 10, CAP_A)
    check("VIP_WE format A", status, "new")
    check("  category", txn.category, "VIP_WE")
    check("  user_id", txn.chat_user_id, "benz4455")
    check("  amount", txn.chat_amount, 400.0)
    check("  chat_id", txn.chat_id, WE)
    check("  msg_id", txn.msg_id, "10")

    # 3. VIP_12 ส่ง format B -> รับเข้าระบบทันทีเช่นกัน
    status, txn2 = process_incoming_slip(db, ["QR_2"], T12, 11, CAP_B)
    check("VIP_12 format B", status, "new")
    check("  category", txn2.category, "VIP_12")
    check("  trans_id", txn2.chat_trans_id, "0000004")
    check("  fullname", txn2.chat_fullname, "Somchai J")
    check("  amount", txn2.chat_amount, 2500.0)

    # 4. รูปเดิมส่งซ้ำในกลุ่มเดิม
    status, _ = process_incoming_slip(db, ["QR_1"], WE, 12, CAP_A)
    check("ส่งรูปเดิมซ้ำ", status, "duplicate")

    # 5. สลิปใบเดิมถูกส่งข้ามไปอีกกลุ่ม
    status, _ = process_incoming_slip(db, ["QR_1"], T12, 13, CAP_A)
    check("สลิปเดิมส่งข้ามกลุ่ม", status, "duplicate")

    # 6. สลิปเดิมถูกมัดรวมกับใบใหม่ (batch id เปลี่ยน แต่ QR ซ้ำ)
    status, _ = process_incoming_slip(db, ["QR_1", "QR_9"], WE, 14, CAP_A)
    check("มัดรวม QR เก่า", status, "duplicate")

    # 7. มัดหลายใบที่ยังไม่เคยใช้ -> ผ่าน
    status, txn3 = process_incoming_slip(db, ["QR_7", "QR_8"], WE, 15, CAP_A)
    check("มัดหลายใบใหม่", status, "new")
    check("  จอง QR ครบ", db.query(UsedQR).filter(UsedQR.batch_id == txn3.batch_id).count(), 2)

    # 8. batch ที่โดน Reject แล้ว ส่งกลับมาใหม่ได้
    rejected = db.query(Transaction).filter(Transaction.batch_id == txn3.batch_id).first()
    rejected.status = "Reject"
    db.commit()
    status, txn4 = process_incoming_slip(db, ["QR_7", "QR_8"], WE, 16, CAP_B)
    check("ส่งซ้ำหลังโดน Reject", status, "new")
    check("  ข้อมูลถูกอัปเดตทับ", txn4.chat_trans_id, "0000004")
    check("  status กลับเป็น pending", txn4.status, "pending")

    # 9. QR ของ batch ที่โดน Reject เอาไปมัดใหม่ได้
    rejected2 = db.query(Transaction).filter(Transaction.batch_id == txn4.batch_id).first()
    rejected2.status = "Reject"
    db.commit()
    status, txn5 = process_incoming_slip(db, ["QR_7"], T12, 17, CAP_A)
    check("QR ที่ถูก Reject เอามาใช้ใหม่", status, "new")
    check("  QR ย้าย batch แล้ว", db.query(UsedQR).filter(UsedQR.qr_ref == "QR_7").first().batch_id, txn5.batch_id)

    # 10. ล็อกการบันทึกชีท: batch ที่บันทึกไปแล้วต้องล็อก แต่พอถูก Reject แล้วส่งใหม่ต้องปลดล็อก
    from database.crud import add_audit_log, is_sheet_locked

    status, txn6 = process_incoming_slip(db, ["QR_LOCK"], WE, 20, CAP_B)
    check("สลิปใหม่ยังไม่ล็อก", is_sheet_locked(db, txn6.batch_id), False)
    add_audit_log(db, txn6.batch_id, "saving_started")
    add_audit_log(db, txn6.batch_id, "sheet_saved")
    check("บันทึกแล้วต้องล็อก", is_sheet_locked(db, txn6.batch_id), True)

    db.query(Transaction).filter(Transaction.batch_id == txn6.batch_id).first().status = "Reject"
    db.commit()
    status, txn7 = process_incoming_slip(db, ["QR_LOCK"], WE, 21, CAP_B)
    check("ส่งใหม่หลัง Reject", status, "new")
    check("  ปลดล็อกให้บันทึกได้อีกครั้ง", is_sheet_locked(db, txn7.batch_id), False)
    add_audit_log(db, txn7.batch_id, "saving_started")
    check("  พอเริ่มบันทึกรอบใหม่ก็ล็อกอีก", is_sheet_locked(db, txn7.batch_id), True)

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
