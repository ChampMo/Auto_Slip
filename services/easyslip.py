import requests
import re
import logging
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


logger = logging.getLogger(__name__)


def _parse_emv_tlv(payload: str) -> dict[str, str]:
    """Parse a simple EMV-style TLV payload into a flat tag/value map."""
    parsed: dict[str, str] = {}
    if not payload:
        return parsed

    index = 0
    payload_length = len(payload)
    while index + 4 <= payload_length:
        tag = payload[index:index + 2]
        length_text = payload[index + 2:index + 4]

        if not tag.isdigit() or not length_text.isdigit():
            break

        value_length = int(length_text)
        start = index + 4
        end = start + value_length
        if end > payload_length:
            break

        parsed[tag] = payload[start:end]
        index = end

    return parsed


def extract_amount_from_qr_payload(qr_payload: str) -> float | None:
    """Try to read the transfer amount directly from the QR payload."""
    if not qr_payload:
        return None

    tags = _parse_emv_tlv(qr_payload)
    amount_text = (tags.get("54") or "").strip()
    if not amount_text:
        return None

    try:
        return float(amount_text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def extract_bank_code_from_receiver_account(recv_acc: dict) -> str:
    """ดึง 4 ตัวท้ายของเลขบัญชีผู้รับ เอาไปเทียบกับ ACCOUNT_MAPPING

    ใช้เลขบัญชีเป็นหลักเสมอ เพราะ ACCOUNT_MAPPING เก็บ "4 ตัวท้ายของเลขบัญชี"
    ส่วน bank.code คือรหัสธนาคาร (คนละความหมาย) จะใช้ก็ต่อเมื่อไม่มีเลขบัญชีมาให้เท่านั้น
    """
    if not isinstance(recv_acc, dict):
        return ""

    bank_info = recv_acc.get("bank") or {}
    if not isinstance(bank_info, dict):
        return ""

    account_value = str(bank_info.get("account", "") or "").strip()
    if account_value:
        digits = re.sub(r"\D", "", account_value)
        return digits[-4:] if len(digits) >= 4 else digits

    code = str(bank_info.get("code", "") or "").strip()
    if re.fullmatch(r"\d{4}", code):
        return code

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
        # ต้องมี timeout เสมอ ไม่งั้นถ้า EasySlip ค้าง จะลากทั้งบอทค้างตามไปด้วย
        # (connect 10 วิ, read 30 วิ)
        response = requests.get(url, headers=headers, params=params, timeout=(10, 30))
        result = response.json()
        payload_amount = extract_amount_from_qr_payload(qr_payload)
        logger.info(
            "EasySlip verify response | payload_amount=%s | status=%s | message=%s",
            payload_amount,
            result.get("status"),
            result.get("message", ""),
        )
        
        # เช็ค status จาก API ว่าเท่ากับ 200 หรือไม่ (ตาม Document)
        if result.get("status") == 200:
            data = result.get("data", {})
            
            # โครงสร้าง Amount
            amount_data = data.get("amount", {})
            if isinstance(amount_data, dict):
                amount = amount_data.get("amount", 0.0)
            else:
                amount = amount_data
            
            # ดึงชื่อคนโอน — บางธนาคารส่งมาแต่ชื่ออังกฤษ หรือส่ง th มาเป็นค่าว่าง
            # ใช้ `or` แทน get(default) เพื่อให้ค่าว่างตกไปใช้ชื่ออังกฤษแทน
            sender = "Unknown"
            if "sender" in data and "account" in data["sender"] and "name" in data["sender"]["account"]:
                name_data = data["sender"]["account"]["name"] or {}
                sender = name_data.get("th") or name_data.get("en") or "ไม่ระบุชื่อ"
            
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
                "payload_amount": payload_amount,
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
                "user_message": user_friendly_msg,
                "payload_amount": payload_amount,
            }
            
    except requests.exceptions.Timeout:
        logger.warning("EasySlip verify timed out | payload_amount=%s", extract_amount_from_qr_payload(qr_payload))
        return {
            "success": False,
            "error": "TIMEOUT",
            "user_message": "Slip verification timed out. The EasySlip service did not respond in time.",
            "payload_amount": extract_amount_from_qr_payload(qr_payload),
        }

    except Exception as e:
        logger.exception("EasySlip verify exception | payload_amount=%s", extract_amount_from_qr_payload(qr_payload))
        return {
            "success": False,
            "error": "EXCEPTION",
            "user_message": f"Network or server error during slip verification: {str(e)}",
            "payload_amount": extract_amount_from_qr_payload(qr_payload),
        }