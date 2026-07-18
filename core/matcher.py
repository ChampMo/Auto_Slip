import re
import hashlib
from sqlalchemy.orm import Session
from database.models import Transaction, UsedQR
from database.crud import get_transaction, add_audit_log

def extract_data_from_caption(caption: str, is_user_group: bool):
    data = {"amount": None, "user_id": None, "trans_id": None, "fullname": None}
    amount_match = re.search(r'(?i)AMOUNT\s*(?:[:]\s*THB|THB\s*[:])\s*([0-9,.]+)', caption)
    if amount_match: data["amount"] = float(amount_match.group(1).replace(',', ''))

    if is_user_group:
        user_match = re.search(r'(?i)User\s*:\s*(\d+)', caption)
        if user_match: data["user_id"] = user_match.group(1)
    else:
        trans_match = re.search(r'(?i)TRANS\s*ID\s*:\s*(\w+)', caption)
        if trans_match: data["trans_id"] = trans_match.group(1)
        name_match = re.search(r'(?is)FULL\s*NAME\s*:\s*(.+?)(?=(?:AMOUNT|$))', caption)
        if name_match: data["fullname"] = name_match.group(1).strip()
    return data

def generate_batch_id(qr_list: list):
    """นำรหัส QR ทุกใบในรูปมาเรียงต่อกันแล้วเข้ารหัสเป็น ID กลุ่ม"""
    combined = "".join(sorted(qr_list))
    return hashlib.md5(combined.encode('utf-8')).hexdigest()

def process_incoming_slip(db: Session, qr_list: list, chat_id: str, msg_id: str, caption: str):
    batch_id = generate_batch_id(qr_list)
    is_user_group = "user" in caption.lower()
    extracted = extract_data_from_caption(caption, is_user_group)

    # 🛑 เช็คซ้ำระดับแยกใบ (ถ้าใบใดใบหนึ่งเคยถูกใช้ใน Batch อื่นไปแล้ว = ซ้ำทั้งก้อน)
    for qr in qr_list:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
        if used and used.batch_id != batch_id:
            add_audit_log(db, batch_id, f"duplicate_qr_found_{qr}")
            return "duplicate", None

    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()

    if txn:
        if txn.status in ["Receive", "Reject", "Duplicate", "matched"]:
            return "duplicate", txn
        
        # ป้องกันกลุ่มเดิมส่งรูปเดิมซ้ำ
        if (is_user_group and txn.g_user_chat_id) or (not is_user_group and txn.g_trans_chat_id):
            return "duplicate", txn
        
        if txn.status == "pending":
            if is_user_group:
                txn.g_user_chat_id = str(chat_id)
                txn.g_user_msg_id = str(msg_id)
                txn.raw_user_caption = caption
                txn.chat_user_id = extracted["user_id"]
                if extracted["amount"]: txn.chat_amount = extracted["amount"]
            else:
                txn.g_trans_chat_id = str(chat_id)
                txn.g_trans_msg_id = str(msg_id)
                txn.raw_trans_caption = caption
                txn.chat_trans_id = extracted["trans_id"]
                txn.chat_fullname = extracted["fullname"]
                if extracted["amount"] and not txn.chat_amount: txn.chat_amount = extracted["amount"]
                
            txn.status = "matched"
            db.commit()
            return "matched", txn
    else:
        # บันทึกกลุ่มใหม่
        new_txn = Transaction(batch_id=batch_id, category="VIP_WE", status="pending")
        if is_user_group:
            new_txn.g_user_chat_id = str(chat_id)
            new_txn.g_user_msg_id = str(msg_id)
            new_txn.raw_user_caption = caption
            new_txn.chat_user_id = extracted["user_id"]
            new_txn.chat_amount = extracted["amount"]
        else:
            new_txn.g_trans_chat_id = str(chat_id)
            new_txn.g_trans_msg_id = str(msg_id)
            new_txn.raw_trans_caption = caption
            new_txn.chat_trans_id = extracted["trans_id"]
            new_txn.chat_fullname = extracted["fullname"]
            new_txn.chat_amount = extracted["amount"] 
            
        db.add(new_txn)
        
        # บันทึก QR ทุกใบลงตาราง UsedQR ว่าผูกกับกลุ่มนี้
        for qr in qr_list:
            db.add(UsedQR(qr_ref=qr, batch_id=batch_id))
            
        db.commit()
        return "pending", new_txn