import requests
from core.config import config
import logging

logger = logging.getLogger(__name__)

def verify_slip(qr_code: str) -> dict:
    """ตรวจสอบสลิปผ่าน EasySlip API"""
    try:
        url = "https://developer.easyslip.com/api/v1/verify" # ปรับ URL ตามที่คุณใช้งานจริง
        headers = {
            "Authorization": f"Bearer {config.EASYSLIP_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {"payload": qr_code}
        
        response = requests.post(url, headers=headers, json=payload)
        data = response.json()
        
        # 💡 ดักจับกรณี API แจ้งว่าโควต้าหมด หรือ Error อื่นๆ
        if response.status_code != 200 or data.get("status") != 200:
            error_msg = data.get("message", str(data))
            
            # เช็กคีย์เวิร์ดที่ EasySlip มักจะส่งมาเวลาโควต้าหมด
            if any(keyword in error_msg.lower() for keyword in ["limit", "quota", "exceed", "credit", "package"]):
                return {"success": False, "error": "QUOTA_EXCEEDED"}
                
            return {"success": False, "error": f"API Error: {error_msg}"}

        # กรณีสำเร็จ
        return {
            "success": True,
            "amount": data["data"]["amount"],
            "sender": data["data"]["sender"]["name"],
            "raw_data": data["data"]
        }
        
    except Exception as e:
        logger.error(f"EasySlip API Exception: {e}")
        return {"success": False, "error": f"Connection Error: {str(e)}"}