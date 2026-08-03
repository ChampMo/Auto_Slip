import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    EASYSLIP_API_KEY = os.getenv("EASYSLIP_API_KEY")
    DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
    GOOGLE_CREDENTIALS = os.getenv("GOOGLE_CREDENTIALS")
    GOOGLE_CREDENTIAL_PATH = os.getenv("GOOGLE_CREDENTIAL_PATH") or os.getenv("GOOGLE_CREDENTIALS")
    GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")


config = Config()