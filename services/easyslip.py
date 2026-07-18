import requests
from core.config import config

def verify_slip(qr_ref: str):
    """
    ส่งรหัส QR ไปตรวจสอบกับ EasySlip API
    """
    # หากใช้ EasySlip V2 Endpoint อาจจะเป็นอีก URL แนะนำให้เช็ค Document ล่าสุดของเขาอีกครั้งครับ
    url = "https://developer.easyslip.com/api/v1/verify" 
    
    headers = {
        "Authorization": f"Bearer {config.EASYSLIP_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "payload": qr_ref
    }

    try:
        # ยิง API ไปที่ EasySlip (ตั้งเวลา Timeout เผื่อเซิร์ฟเวอร์เขาช้า)
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        # เช็คสถานะการตรวจสอบ (200 คือสำเร็จ)
        if data.get("status") == 200:
            payload_data = data.get("data", {})
            
            # แกะข้อมูลที่ต้องการ (โครงสร้าง JSON อาจเปลี่ยนตามเวอร์ชัน API ให้ปรับแก้ตามจริง)
            amount = payload_data.get("amount", 0.0)
            sender_name = payload_data.get("sender", {}).get("account", {}).get("name", {"th": "ไม่ระบุ"})
            
            # หากชื่อมาเป็น Dictionary (เช่น {'th': 'นาย เอ', 'en': 'Mr. A'}) ให้ดึงภาษาไทยมา
            if isinstance(sender_name, dict):
                sender_name = sender_name.get("th", str(sender_name))

            return {
                "success": True,
                "amount": float(amount),
                "sender": sender_name
            }
        else:
            return {"success": False, "error": data.get("message", "ไม่สามารถตรวจสอบสลิปได้")}

    except Exception as e:
        print(f"❌ EasySlip API Error: {e}")
        return {"success": False, "error": str(e)}