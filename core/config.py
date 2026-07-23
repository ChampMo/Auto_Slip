import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY")
    DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
    ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
    GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")

config = Config()