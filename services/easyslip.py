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
            sender = "ไม่ระบุชื่อ"
            if "sender" in data and "account" in data["sender"] and "name" in data["sender"]["account"]:
                name_data = data["sender"]["account"]["name"]
                sender = name_data.get("th", name_data.get("en", "ไม่ระบุชื่อ"))
                
            # 👇 --- เพิ่มโค้ดชุดนี้สำหรับดึงข้อมูล "ผู้รับ" ---
            receiver_info = "-"
            if "receiver" in data and "account" in data["receiver"]:
                recv_acc = data["receiver"]["account"]
                
                # 1. ลองหา "เลขบัญชี" ก่อน
                if "bank" in recv_acc and "account" in recv_acc["bank"]:
                    raw_acc = recv_acc["bank"]["account"]  # API จะให้มาเป็น "xxx-x-x4662-x"
                    
                    # 💡 ใช้ Regex สกัดเฉพาะ "ตัวเลข" ออกมา
                    # 💡 ใช้ Regex สกัดเฉพาะตัวเลข
                    extracted_digits = re.sub(r'\D', '', raw_acc)

                    receiver_account = extracted_digits if extracted_digits else raw_acc

                    # ใช้เลข 4 ตัวท้ายในการค้นหา
                    receiver_info = ACCOUNT_MAPPING.get(
                        receiver_account[-4:],
                        receiver_account[-4:]
                    )
                    
                # 2. ถ้าไม่มีเลขบัญชี (เช่น ทรูมันนี่) ให้ดึง "ชื่อ" มาแทน
                elif "name" in recv_acc and "th" in recv_acc["name"]:
                    receiver_info = recv_acc["name"]["th"]
            # 👆 -----------------------------------------
                
            return {
                "success": True,
                "amount": float(amount),
                "sender": sender,
                "receiver": receiver_info,  # 👈 ส่งค่าที่ดึงได้กลับไปด้วย
                "raw_data": data
            }
        else:
            # 🛑 ปรับปรุง UX: แปลง Error จาก API เป็นภาษาไทยให้ User เข้าใจง่าย
            raw_error_msg = result.get("message", "").upper()
            status_code = result.get("status")
            
            # ดักจับ Error ยอดฮิตและแปลความหมาย
            if "QUOTA" in raw_error_msg or "EXCEEDED" in raw_error_msg:
                user_friendly_msg = "⚠️ โควต้าการตรวจสอบสลิปหมด หรือแพ็กเกจ EasySlip หมดอายุแล้ว กรุณาต่ออายุแพ็กเกจ"
                error_type = "QUOTA_EXCEEDED"
            elif "UNAUTHORIZED" in raw_error_msg or status_code == 401:
                user_friendly_msg = "🔒 การยืนยันตัวตนล้มเหลว (API Key ของ EasySlip ไม่ถูกต้อง)"
                error_type = "UNAUTHORIZED"
            elif "NOT FOUND" in raw_error_msg or "INVALID" in raw_error_msg:
                user_friendly_msg = "❌ ไม่พบข้อมูลสลิปนี้ในระบบธนาคาร หรือ QR Code ไม่ถูกต้อง (อาจเป็นสลิปปลอม)"
                error_type = "INVALID_SLIP"
            elif "MAINTENANCE" in raw_error_msg:
                user_friendly_msg = "🛠️ ระบบ API หรือธนาคารต้นทางกำลังปรับปรุงชั่วคราว"
                error_type = "MAINTENANCE"
            else:
                user_friendly_msg = f"🚨 ระบบตรวจสอบขัดข้องจากทาง API (ข้อความ: {result.get('message')})"
                error_type = "UNKNOWN_ERROR"

            return {
                "success": False,
                "error": error_type, # คืนค่า Code สั้นๆ เผื่อเอาไปเขียน if-else ในไฟล์อื่น
                "user_message": user_friendly_msg # คืนค่าข้อความภาษาไทยสวยๆ ไปแสดงผล
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": "EXCEPTION",
            "user_message": f"🌐 เกิดข้อผิดพลาดในการเชื่อมต่ออินเทอร์เน็ตหรือเซิร์ฟเวอร์: {str(e)}"
        }