import logging
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
from core.config import config
from services.gdrive import drive_service

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

class GoogleSheetsService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GoogleSheetsService, cls).__new__(cls)
            cls._instance._init_service()
        return cls._instance

    def _init_service(self):
        """เริ่มต้นการเชื่อมต่อ gspread ด้วย Service Account"""
        try:
            creds = Credentials.from_service_account_file(config.GOOGLE_CREDS_PATH, scopes=SCOPES)
            self.client = gspread.authorize(creds)
            
            # --- In-Memory Cache เพื่อความเร็วในการทำงาน ---
            self._cached_year = None
            self._cached_year_folder_id = None
            self._cached_month = None
            self._cached_spreadsheet_id = None
            
            logger.info("Google Sheets Service initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets Service: {e}")
            raise e

    def _get_dynamic_spreadsheet(self) -> gspread.Spreadsheet:
        """ค้นหาหรือสร้างไฟล์ Spreadsheet ประจำเดือนผ่าน Cache และ Drive Service อัตโนมัติ"""
        now = datetime.now()
        current_year = now.strftime("%Y")      
        current_month_name = now.strftime("%Y-%m") 

        # 1. ตรวจสอบ/อัปเดต Cache โฟลเดอร์ปี
        if self._cached_year != current_year or not self._cached_year_folder_id:
            logger.info(f"Cache miss for year folder. Fetching/Creating for {current_year}...")
            self._cached_year_folder_id = drive_service.get_or_create_year_folder(current_year)
            self._cached_year = current_year

        # 2. ตรวจสอบ/อัปเดต Cache ชีตประจำเดือน
        if self._cached_month != current_month_name or not self._cached_spreadsheet_id:
            logger.info(f"Cache miss for month sheet. Fetching for {current_month_name}...")
            
            # ตอนนี้ดึงข้อมูลจากตัวใหม่ที่ครอบคลุมการสร้างไฟล์ในตัวไว้แล้ว
            sheet_id = drive_service.get_or_create_month_sheet(
                self._cached_year_folder_id, 
                current_month_name
            )
            
            if not sheet_id:
                raise Exception("Failed to get or create dynamic spreadsheet via Drive Service.")
                
            self._cached_spreadsheet_id = sheet_id
            self._cached_month = current_month_name

        # 3. เปิดใช้งาน Spreadsheet จาก ID ที่ส่งตรงมาจาก Drive Service
        return self.client.open_by_key(self._cached_spreadsheet_id)

    def append_to_sheet(self, txn) -> bool:
        """ตรวจเช็กแท็บวัน และบันทึกข้อมูลสลิปที่อนุมัติ/ปฏิเสธ ลง Google Sheets"""
        try:
            # ดึงไฟล์ Spreadsheet ประจำเดือนปัจจุบัน
            spreadsheet = self._get_dynamic_spreadsheet()
            
            now = datetime.now()
            day_tab_name = now.strftime("%d") # เช่น "20" หรือ "01"
            
            # ตรวจสอบว่ามีแท็บของวันนั้นๆ หรือยัง
            try:
                worksheet = spreadsheet.worksheet(day_tab_name)
            except gspread.exceptions.WorksheetNotFound:
                logger.info(f"Worksheet tab '{day_tab_name}' not found. Creating new tab...")
                # สร้างแท็บใหม่
                worksheet = spreadsheet.add_worksheet(title=day_tab_name, rows="1000", cols="10")
                
                # ใส่ Header แรกเริ่มของวันในแถวแรกสุด
                headers = [
                    "วันที่บันทึก", "เวลาบันทึก", "Telegram User ID", 
                    "Transaction ID (Chat)", "ชื่อผู้แจ้ง (Chat)", 
                    "ชื่อผู้โอนจริง (API)", "ยอดเงิน", "สถานะ", "Batch ID"
                ]
                worksheet.append_row(headers)
            
            # จัดเตรียมข้อมูลเหมือนโครงสร้างเดิมของคุณ
            date_str = now.strftime("%d/%m/%Y")
            time_str = now.strftime("%H:%M:%S")
            
            row = [
                date_str,                                
                time_str,                                
                txn.chat_user_id or "-",            
                txn.chat_trans_id or "-",           
                txn.chat_fullname or "-",           
                txn.sender_names or "-",             
                txn.api_total_amount or txn.chat_amount, 
                txn.status,                          
                txn.batch_id                         
            ]
            
            # สั่งเพิ่มข้อมูลต่อท้ายบรรทัดล่างสุดของแท็บวันนั้นๆ
            worksheet.append_row(row)
            logger.info(f"Successfully appended row to tab '{day_tab_name}' in sheet '{self._cached_month}'")
            return True
            
        except Exception as e:
            logger.error(f"⚠️ Append to Sheet Error: {e}")
            return False

# สร้าง Instance หลักเพื่อให้รองรับการเรียกใช้ฟังก์ชันแบบเดิมใน bot/handlers.py
sheets_service = GoogleSheetsService()

def append_to_sheet(txn) -> bool:
    """Wrapper function สำหรับเรียกใช้ผ่านโครงสร้างโค้ดเดิมในบอท"""
    return sheets_service.append_to_sheet(txn)