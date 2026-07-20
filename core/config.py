import os
from anyio import Path
from dotenv import load_dotenv

load_dotenv()
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# โหลด .env โดยใช้ os.path.join
load_dotenv(dotenv_path=os.path.join(BASE_DIR, ".env"))

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY")
    # SPREADSHEET_ID = os.getenv("SPREADSHEET_ID") # เพิ่มบรรทัดนี้

    MAIN_FOLDER_ID = os.getenv("MAIN_FOLDER_ID") 
    
    # Path ไปยังไฟล์คีย์ Service Account สำหรับเชื่อมต่อ Google API
    GOOGLE_CREDS_PATH = os.getenv("GOOGLE_CREDS_PATH", os.path.join(BASE_DIR, "credentials.json"))

config = Config()