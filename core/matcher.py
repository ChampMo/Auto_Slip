import re
import hashlib
from sqlalchemy.orm import Session
from database.models import Transaction, UsedQR
from database.crud import get_transaction, add_audit_log


GROUP_CONFIG = {
    "-1004418034373": {"category": "VIP_WE", "is_user": True},   # กลุ่ม User ของ Category A
    "-1003573441688": {"category": "VIP_WE", "is_user": False},  # กลุ่ม Trans ของ Category A
    "-5153291438": {"category": "VIP_12", "is_user": True},   # กลุ่ม User ของ Category B
    "-5356341183": {"category": "VIP_12", "is_user": False},  # กลุ่ม Trans ของ Category B
}


def extract_data_from_caption(caption: str, is_user_group: bool):
    data = {"amount": None, "user_id": None, "trans_id": None, "fullname": None}
    
    amount_match = re.search(r'(?i)AMOUNT\s*(?:[:=]\s*)?(?:THB\s*(?:[:=]\s*)?)?([0-9,.]+)', caption)
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
    chat_id_str = str(chat_id)
    
    group_info = GROUP_CONFIG.get(chat_id_str)
    if group_info:
        category = group_info["category"]
        is_user_group = group_info["is_user"]
    else:
        category = "UNKNOWN"
        is_user_group = "user" in caption.lower()

    batch_id = generate_batch_id(qr_list)
    extracted = extract_data_from_caption(caption, is_user_group)

    # 🛑 จุดที่ 1: เช็คซ้ำระดับแยกใบ (ข้ามกลุ่ม/มัดรวมใหม่)
    for qr in qr_list:
        used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
        if used and used.batch_id != batch_id:
            old_txn = db.query(Transaction).filter(Transaction.batch_id == used.batch_id).first()
            # 💡 ปลดล็อค: ถ้าสลิปเก่าเคยถูก Reject ไปแล้ว ให้ถือว่าเอามาใช้ใหม่ได้ ไม่ซ้ำ!
            if old_txn and old_txn.status.lower() not in ["reject", "rejected"]:
                add_audit_log(db, batch_id, f"duplicate_qr_found_{qr}")
                return "duplicate", None

    txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()

    if txn:
        # 🛑 จุดที่ 2: ถ้ารูปเดิมเป๊ะ (batch_id เดิม)
        # 💡 ปลดล็อค: เอา "Reject" ออกจากเงื่อนไขบล็อก เพื่อให้เอาสลิปเดิมส่งมาแก้ตัวได้
        if txn.status.lower() in ["receive", "duplicate", "matched"]:
            return "duplicate", txn
            
        if txn.category != category and txn.category != "UNKNOWN":
            add_audit_log(db, batch_id, f"cross_category_error_{txn.category}_to_{category}")
            return "duplicate", txn
        
        # ป้องกันกลุ่มเดิมส่งรูปเดิมซ้ำ (ยกเว้นเคสที่เพิ่งโดน Reject มา ให้ส่งซ้ำได้)
        if txn.status.lower() not in ["reject", "rejected"]:
            if (is_user_group and txn.g_user_chat_id) or (not is_user_group and txn.g_trans_chat_id):
                return "duplicate", txn
        
        # 💡 จุดที่ 3: กรณี Pending หรือ Reject ให้รับข้อมูลใหม่เข้าไปอัปเดตทับของเดิม
        if txn.status.lower() in ["pending", "reject", "rejected"]:
                
            if is_user_group:
                txn.g_user_chat_id = chat_id_str
                txn.g_user_msg_id = str(msg_id)
                txn.raw_user_caption = caption
                txn.chat_user_id = extracted["user_id"]
                if extracted["amount"]: txn.chat_amount = extracted["amount"]
            else:
                txn.g_trans_chat_id = chat_id_str
                txn.g_trans_msg_id = str(msg_id)
                txn.raw_trans_caption = caption
                txn.chat_trans_id = extracted["trans_id"]
                txn.chat_fullname = extracted["fullname"]
                # เอา amount ใหม่มาทับเผื่อเขาพิมพ์แก้ยอดมาให้ตรง
                if extracted["amount"]: txn.chat_amount = extracted["amount"]
            
            # เช็คว่าหลังจากอัปเดตแล้ว มีคนส่งครบ 2 ฝั่งหรือยัง ถ้าครบให้เปลี่ยนสถานะไปจับคู่ใหม่
            if txn.g_user_chat_id and txn.g_trans_chat_id:
                txn.status = "matched"
            else:
                txn.status = "pending"
                
            db.commit()
            return txn.status, txn
    else:
        # บันทึกกลุ่มใหม่ (First time)
        new_txn = Transaction(batch_id=batch_id, category=category, status="pending")
        
        if is_user_group:
            new_txn.g_user_chat_id = chat_id_str
            new_txn.g_user_msg_id = str(msg_id)
            new_txn.raw_user_caption = caption
            new_txn.chat_user_id = extracted["user_id"]
            new_txn.chat_amount = extracted["amount"]
        else:
            new_txn.g_trans_chat_id = chat_id_str
            new_txn.g_trans_msg_id = str(msg_id)
            new_txn.raw_trans_caption = caption
            new_txn.chat_trans_id = extracted["trans_id"]
            new_txn.chat_fullname = extracted["fullname"]
            new_txn.chat_amount = extracted["amount"] 
            
        db.add(new_txn)
        
        # บันทึก QR ลงตาราง (รองรับกรณีเป็น QR เก่าที่เคยถูก Reject แล้วเอามามัดรวมใหม่)
        for qr in qr_list:
            used = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
            if used:
                used.batch_id = batch_id # โยก QR มาเป็นของ batch ใหม่แทน
            else:
                db.add(UsedQR(qr_ref=qr, batch_id=batch_id))
            
        db.commit()
        return "pending", new_txn