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
            creds = Credentials.from_service_account_file(config.GOOGLE_CREDENTIALS, scopes=SCOPES)
            self.client = gspread.authorize(creds)
            
            # --- In-Memory Cache เพื่อความเร็วในการทำงาน ---
            self._cached_month = None
            self._cached_spreadsheet_id = None
            
            logger.info("Google Sheets Service initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets Service: {e}")
            raise e

    def _get_dynamic_spreadsheet(self) -> gspread.Spreadsheet:
        """ค้นหาไฟล์ Spreadsheet ประจำเดือนผ่านการสแกนชื่อใน Drive อัตโนมัติ"""
        now = datetime.now()
        file_name = f"Slips_{now.strftime('%m-%Y')}" # เช่น Slips_07-2026

        # ตรวจสอบ Cache ถ้าเปลี่ยนเดือนหรือยังไม่มี ID ให้วิ่งไปค้นหาใหม่
        if self._cached_month != file_name or not self._cached_spreadsheet_id:
            logger.info(f"Cache miss for month sheet. Searching file ID for: {file_name}")
            
            # 🔍 ดึง ID จากชื่อไฟล์ในไดรฟ์ผ่าน Drive Service
            sheet_id = drive_service.get_spreadsheet_id_by_name(file_name)
            
            if not sheet_id:
                # 💡 จัดรูปแบบข้อความ UX ให้ชัดเจนและแนะนำวิธีแก้ไขทันที
                error_detail = (
                    f"ไม่พบไฟล์ชื่อ '{file_name}' ใน Google Drive\n\n"
                )
                raise gspread.exceptions.SpreadsheetNotFound(error_detail)
                
            self._cached_spreadsheet_id = sheet_id
            self._cached_month = file_name

        # เปิดใช้งาน Spreadsheet จาก ID ที่ดึงมาได้
        return self.client.open_by_key(self._cached_spreadsheet_id)

    def append_to_sheet(self, txn) -> tuple[bool, str]:
        """ตรวจเช็กแท็บวัน และบันทึกข้อมูลสลิป ลง Google Sheets"""
        try:
            # 1. 🗂️ ค้นหาไฟล์ประจำเดือน
            try:
                spreadsheet = self._get_dynamic_spreadsheet()
            except Exception as e:
                # ดึงข้อความ error_detail ที่เราออกแบบไว้ส่งกลับไป
                error_msg = str(e)
                print(f"❌ {error_msg}")
                return False, error_msg
            
            now = datetime.now()
            sheet_name = now.strftime("%d") # ใช้ชื่อชีทเป็นวัน (เช่น "21")
            
            # 2. 📄 ค้นหาชีท หรือ สร้างชีทใหม่ถ้าขึ้นวันใหม่
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                print(f"📄 กำลังสร้างชีทสำหรับวันที่ {sheet_name}...")
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=20)
                
                # ใส่หัวข้อคอลัมน์อัตโนมัติเมื่อสร้างชีทใหม่
                headers = ["วันที่", "เวลา", "รหัสลูกค้า", "Trans ID", "ชื่อลูกค้า", "ชื่อคนโอน", "ยอดเงิน", "สถานะ", "Batch ID"]
                worksheet.append_row(headers)
                
                # ลบชีทขยะ "Sheet1" ที่แถมมาตอนสร้างไฟล์ทิ้ง
                try:
                    sheet1 = spreadsheet.worksheet("Sheet1")
                    spreadsheet.del_worksheet(sheet1)
                except:
                    pass

            # 3. ✍️ เตรียมข้อมูลและบันทึกลงชีทของวันนี้
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
            
            worksheet.append_row(row)
            print(f"✅ บันทึกลง Sheet สำเร็จ (วันที่ {sheet_name})")
            return True, ""
            
        except Exception as e:
            error_msg = f"Google Sheets Error: {e}"
            print(f"⚠️ {error_msg}")
            return False, error_msg

# สร้าง Instance หลักสำหรับเรียกใช้งาน
sheets_service = GoogleSheetsService()

def append_to_sheet(txn) -> tuple[bool, str]:
    """Wrapper function สำหรับเรียกใช้ผ่านโครงสร้างโค้ดเดิมใน bot/handlers.py"""
    return sheets_service.append_to_sheet(txn)