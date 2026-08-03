import requests
import re
from core.config import config

ACCOUNT_MAPPING = {
    "5111": "SCB-CP",
    "9298": "BAY-CKB",
    "2872": "KB-CKB",
    "7726": "KB-CKB97726",
    "9349": "KKP-Jak",
    "9809": "GSB-Jak",
    "1307": "BBL-Ploy",
    "9877": "GSB-Ativit",
    "2984": "KKP-LS",
    "0009": "KB-BS",
    "6523": "GSB-Teera",
    "3872": "GSB-Yo",
    "8514": "TTB-Yo",
    "9057": "SCB-Yo",
    "0914": "TTB-Jak",
    "0234": "SCB-MT20234",
    "7446": "KB-CP37446",
    "5761": "KB-CP05761",
}

BANK_DROPDOWN_VALUES = sorted(set(ACCOUNT_MAPPING.values()))


def extract_bank_code_from_receiver_account(recv_acc: dict) -> str:
    """Extract a 4-digit bank code from the receiver account payload if available."""
    if not isinstance(recv_acc, dict):
        return ""

    bank_info = recv_acc.get("bank") or {}
    if isinstance(bank_info, dict):
        code = str(bank_info.get("code", "") or "").strip()
        if re.fullmatch(r"\d{4}", code):
            return code

        account_value = str(bank_info.get("account", "") or "").strip()
        if account_value:
            digits = re.sub(r"\D", "", account_value)
            return digits[-4:] if len(digits) >= 4 else digits

    return ""


def verify_slip(qr_payload: str) -> dict:
    # URL ตาม Document (v1/verify)
    url = "https://api.easyslip.com/v1/verify" 
    
    headers = {
        "Authorization": f"Bearer {config.EASYSLIP_API_KEY}"
    }
    
    # ส่งเป็น params ตามตัวอย่างในหน้าเว็บ
    params = {
        "payload": qr_payload
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        result = response.json()
        
        # เช็ค status จาก API ว่าเท่ากับ 200 หรือไม่ (ตาม Document)
        if result.get("status") == 200:
            data = result.get("data", {})
            
            # โครงสร้าง Amount
            amount_data = data.get("amount", {})
            if isinstance(amount_data, dict):
                amount = amount_data.get("amount", 0.0)
            else:
                amount = amount_data
            
            # ดึงชื่อคนโอน
            sender = "Unknown"
            if "sender" in data and "account" in data["sender"] and "name" in data["sender"]["account"]:
                name_data = data["sender"]["account"]["name"]
                sender = name_data.get("th", name_data.get("en", "ไม่ระบุชื่อ"))
            
            # 👇 --- เพิ่มโค้ดชุดนี้สำหรับดึงข้อมูล "ผู้รับ" ---
            receiver_info = "-"
            receiver_bank_code = ""
            receiver_bank_matches = False
            if "receiver" in data and "account" in data["receiver"]:
                recv_acc = data["receiver"]["account"]
                
                # 1. ลองหา 4 หลักธนาคารก่อนจากข้อมูล bank.code ก่อน
                receiver_bank_code = extract_bank_code_from_receiver_account(recv_acc)
                if receiver_bank_code:
                    mapped_bank = ACCOUNT_MAPPING.get(receiver_bank_code)
                    if mapped_bank:
                        receiver_info = mapped_bank
                        receiver_bank_matches = True
                    else:
                        receiver_info = receiver_bank_code
                
                # 2. ถ้าไม่มีเลขบัญชี (เช่น ทรูมันนี่) ให้ดึง "ชื่อ" มาแทน
                elif "name" in recv_acc and "th" in recv_acc["name"]:
                    receiver_info = recv_acc["name"]["th"]
            # 👆 -----------------------------------------
                
            return {
                "success": True,
                "amount": float(amount),
                "sender": sender,
                "receiver": receiver_info,  # 👈 ส่งค่าที่ดึงได้กลับไปด้วย
                "receiver_bank_code": receiver_bank_code,
                "receiver_bank_matches": receiver_bank_matches,
                "raw_data": data
            }
        else:
            # Convert API error messages into user-friendly English messages
            raw_error_msg = result.get("message", "").upper()
            status_code = result.get("status")

            if "QUOTA" in raw_error_msg or "EXCEEDED" in raw_error_msg:
                user_friendly_msg = "Quota exceeded or EasySlip package expired. Please renew your subscription."
                error_type = "QUOTA_EXCEEDED"
            elif "UNAUTHORIZED" in raw_error_msg or status_code == 401:
                user_friendly_msg = "Authentication failed (invalid EasySlip API key)."
                error_type = "UNAUTHORIZED"
            elif "NOT FOUND" in raw_error_msg or "INVALID" in raw_error_msg:
                user_friendly_msg = "Slip not found in bank records, or QR code is invalid (possible fake slip)."
                error_type = "INVALID_SLIP"
            elif "MAINTENANCE" in raw_error_msg:
                user_friendly_msg = "Verification service or source bank is under maintenance. Please try again later."
                error_type = "MAINTENANCE"
            else:
                user_friendly_msg = f"Slip verification API error (message: {result.get('message')})"
                error_type = "UNKNOWN_ERROR"

            return {
                "success": False,
                "error": error_type,
                "user_message": user_friendly_msg
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": "EXCEPTION",
            "user_message": f"Network or server error during slip verification: {str(e)}"
        }