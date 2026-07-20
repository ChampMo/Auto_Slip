import requests
from core.config import config

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
        # 🛑 จุดสำคัญ: เปลี่ยนมาใช้ requests.get()
        response = requests.get(url, headers=headers, params=params)
        
        result = response.json()
        
        # เช็ค status จาก API ว่าเท่ากับ 200 หรือไม่ (ตาม Document)
        if result.get("status") == 200:
            data = result.get("data", {})
            
            # 🛑 จุดสำคัญ: โครงสร้าง Amount ของเค้าซ้อนกัน 2 ชั้น
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
                
            return {
                "success": True,
                "amount": float(amount),
                "sender": sender,
                "raw_data": data
            }
        else:
            # กรณี Error จาก API
            return {
                "success": False,
                "error": result.get("message", "API Error")
            }
            
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }