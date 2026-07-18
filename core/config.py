import os
from dotenv import load_dotenv

# โหลดค่าตัวแปรจากไฟล์ .env ขึ้นมาไว้ในระบบ
load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY", "")
    
    # ถ้าใน .env ไม่มีค่า DATABASE_URL จะใช้ sqlite เป็นค่าเริ่มต้น
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./slips_data.db")

# สร้าง Object ไว้รอเรียกใช้งาน
config = Config()