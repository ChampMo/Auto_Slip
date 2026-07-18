import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY")
    SPREADSHEET_ID = os.getenv("SPREADSHEET_ID") # เพิ่มบรรทัดนี้

config = Config()