import re
from sqlalchemy.orm import Session
from database.models import Transaction
from database.crud import get_transaction, add_audit_log

def extract_data_from_caption(caption: str, is_user_group: bool):
    """
    ฟังก์ชันสกัดข้อมูลจากข้อความด้วย Regex 
    รองรับเคสที่ข้อความติดกัน เช่น '65090500452TTB' หรือ '0000001FULL NAME'
    """
    data = {
        "amount": None,
        "user_id": None,
        "trans_id": None,
        "fullname": None
    }
    
    # 1. ดึงยอดเงิน (รองรับทั้ง 'Amount : THB 200' และ 'AMOUNT THB : 200.00')
    # อธิบาย Regex: หาคำว่า AMOUNT ตามด้วย THB (สลับที่หรือมี : คั่นได้) แล้วจับตัวเลขด้านหลัง
    amount_match = re.search(r'(?i)AMOUNT\s*(?:[:]\s*THB|THB\s*[:])\s*([0-9,.]+)', caption)
    if amount_match:
        data["amount"] = float(amount_match.group(1).replace(',', ''))

    if is_user_group:
        # 2. ดึง User ID (ตัวเลขยาวๆ ที่อยู่หลัง User :)
        # อธิบาย Regex: ดักจับเฉพาะ "ตัวเลข" (\d+) ที่อยู่ติดกันหลังคำว่า User :
        user_match = re.search(r'(?i)User\s*:\s*(\d+)', caption)
        if user_match:
            data["user_id"] = user_match.group(1)
    else:
        # 3. ดึง Trans ID (ตัวเลขหรือตัวอักษร ที่อยู่หลัง TRANS ID :)
        trans_match = re.search(r'(?i)TRANS\s*ID\s*:\s*(\w+)', caption)
        if trans_match:
            data["trans_id"] = trans_match.group(1)
            
        # 4. ดึง ชื่อ-นามสกุล (แก้เพิ่ม (?is) เข้าไปด้านหน้าสุด)
        name_match = re.search(r'(?is)FULL\s*NAME\s*:\s*(.+?)(?=(?:AMOUNT|$))', caption)
        if name_match:
            data["fullname"] = name_match.group(1).strip()

    return data


def process_incoming_slip(db: Session, qr_ref: str, chat_id: str, msg_id: str, caption: str):
    txn = get_transaction(db, qr_ref)
    
    # แยกแยะว่าเป็นสลิปจากกลุ่ม User ID หรือกลุ่ม Trans ID
    is_user_group = "user" in caption.lower()
    
    # สกัดข้อมูลจากข้อความด้วยฟังก์ชันใหม่
    extracted = extract_data_from_caption(caption, is_user_group)

    if txn:
        # --- ด่านที่ 3: ตรวจสลิปซ้ำ ---
        if txn.status in ["Receive", "Reject", "Duplicate", "matched"]:
            add_audit_log(db, qr_ref, f"duplicate_rejected_from_{chat_id}")
            return "duplicate", txn
        
        # --- ด่านที่ 4: จับคู่ (Matching) ---
        if txn.status == "pending":
            
            # 🛑 [เพิ่มใหม่] เช็คว่าส่งซ้ำมาจากกลุ่มฝั่งเดิมที่เคยส่งมาแล้วหรือไม่?
            if (is_user_group and txn.g_user_chat_id) or (not is_user_group and txn.g_trans_chat_id):
                add_audit_log(db, qr_ref, f"duplicate_pending_from_{chat_id}")
                return "duplicate", txn  # ส่งซ้ำฝั่งเดิม ให้แจ้งเตือน Duplicate ลงในกลุ่มนั้นไปเลย!

            # หากมาจากฝั่งตรงข้ามจริงๆ ถึงจะยอมให้จับคู่
            if is_user_group:
                txn.g_user_chat_id = str(chat_id)
                txn.g_user_msg_id = str(msg_id)
                txn.raw_user_caption = caption
                txn.chat_user_id = extracted["user_id"]
                if extracted["amount"]: 
                    txn.chat_amount = extracted["amount"]
            else:
                txn.g_trans_chat_id = str(chat_id)
                txn.g_trans_msg_id = str(msg_id)
                txn.raw_trans_caption = caption
                txn.chat_trans_id = extracted["trans_id"]
                txn.chat_fullname = extracted["fullname"]
                if extracted["amount"] and not txn.chat_amount: 
                    txn.chat_amount = extracted["amount"] 
                
            txn.status = "matched"
            db.commit()
            add_audit_log(db, qr_ref, f"matched_with_{chat_id}")
            return "matched", txn
    else:
        # --- เจอสลิปครั้งแรก (บันทึกรอคู่) ---
        new_txn = Transaction(
            qr_ref=qr_ref,
            category="VIP_WE", 
            status="pending"
        )
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
        db.commit()
        add_audit_log(db, qr_ref, f"received_first_slip_from_{chat_id}")
        return "pending", new_txn