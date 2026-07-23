from datetime import datetime
import logging
from typing import Tuple, Any, Dict

from core.config import config
from google.oauth2.service_account import Credentials
import gspread
from services.gdrive import drive_service

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
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
            creds = Credentials.from_service_account_file(
                config.GOOGLE_CREDENTIALS, scopes=SCOPES
            )
            self.client = gspread.authorize(creds)
            self._cached_month = None
            self._cached_spreadsheet_id = None
            logger.info("Google Sheets Service initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets Service: {e}")
            raise e

    def _get_dynamic_spreadsheet(self) -> gspread.Spreadsheet:
        """ค้นหาและ Caching ไฟล์ Spreadsheet ประจำเดือนผ่าน Drive"""
        now = datetime.now()
        file_name = f"Slips_{now.strftime('%m-%Y')}"

        if (
            self._cached_month != file_name
            or not self._cached_spreadsheet_id
        ):
            logger.info(
                f"Cache miss for month sheet. Searching file ID for: {file_name}"
            )
            sheet_id = drive_service.get_spreadsheet_id_by_name(file_name)

            if not sheet_id:
                raise gspread.exceptions.SpreadsheetNotFound(
                    f"ไม่พบไฟล์ชื่อ '{file_name}' ใน Google Drive"
                )

            self._cached_spreadsheet_id = sheet_id
            self._cached_month = file_name

        return self.client.open_by_key(self._cached_spreadsheet_id)

    @staticmethod
    def _parse_float(val: Any) -> float:
        """Helper แปลงค่าใน Cell เป็น float อย่างปลอดภัย"""
        try:
            if isinstance(val, str):
                val = val.replace(",", "").strip()
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    def _apply_styles(self, worksheet: gspread.Worksheet):
        """ใส่เส้นขอบตารางเฉพาะบริเวณที่มีข้อมูล"""
        try:
            # 1. หาแถวล่าสุดที่มีข้อมูลจริง
            last_row = len(worksheet.col_values(1))
            if last_row < 1:
                return

            # 2. รูปแบบเส้นขอบ
            border_format = {
                "borders": {
                    "top": {"style": "SOLID"},
                    "bottom": {"style": "SOLID"},
                    "left": {"style": "SOLID"},
                    "right": {"style": "SOLID"},
                    "innerHorizontal": {"style": "SOLID"},
                    "innerVertical": {"style": "SOLID"},
                }
            }

            # 3. ตีเส้นเฉพาะ A1 จนถึงคอลัมน์ H ในแถวล่าสุดที่มีข้อความ
            worksheet.format(f"A1:H{last_row}", border_format)
            
            logger.info(f"📐 ใส่เส้นตารางเฉพาะแถวที่มีข้อมูล (A1:H{last_row}) สำเร็จ")

        except Exception as e:
            logger.error(f"Formatting failed: {e}", exc_info=True)

    def update_daily_summary(self, worksheet: gspread.Worksheet):
        """สร้าง/อัปเดต ตารางสรุปยอดประจำวัน ที่ Column L"""
        records = worksheet.get_all_values()
        if len(records) <= 1:
            return

        user_count, user_total = 0, 0.0
        trans_count, trans_total = 0, 0.0
        customers: Dict[str, Dict[str, float]] = {}

        # วนลูปอ่านข้อมูลข้าม Header (Row 1)
        for row in records[1:]:
            col_user_id = row[0] if len(row) > 0 else ""
            col_user_amt = row[1] if len(row) > 1 else ""
            col_trans_id = row[4] if len(row) > 4 else ""
            col_trans_amt = row[5] if len(row) > 5 else ""

            # คำนวณฝั่ง USER
            if col_user_id:
                user_count += 1
                user_total += self._parse_float(col_user_amt)

            # คำนวณฝั่ง TRANS
            if col_trans_id:
                trans_count += 1
                amt = self._parse_float(col_trans_amt)
                trans_total += amt

                if col_trans_id not in customers:
                    customers[col_trans_id] = {"count": 0, "total": 0.0}

                customers[col_trans_id]["count"] += 1
                customers[col_trans_id]["total"] += amt

        # จัดโครงสร้างตาราง Summary
        summary = [
            ["สรุปรายวัน"],
            ["วันที่", datetime.now().strftime("%d/%m/%Y")],
            [],
            ["USER"],
            ["จำนวนรายการ", user_count],
            ["ยอดเงินรวม", user_total],
            [],
            ["TRANS"],
            ["จำนวนรายการ", trans_count],
            ["ยอดเงินรวม", trans_total],
            [],
            ["ลูกค้าประจำ"],
            ["ลูกค้า", "จำนวนครั้ง", "ยอดรวม"],
        ]

        # เพิ่มข้อมูลลูกค้าประจำ (ใช้บริการ >= 3 ครั้ง)
        for name, data in customers.items():
            if data["count"] >= 3:
                summary.append([name, data["count"], data["total"]])

        # เขียนข้อมูลกลับไปยัง Column L1:N
        end_row = len(summary)
        worksheet.update(f"L1:N{end_row}", summary)

    def append_to_sheet(self, txn) -> Tuple[bool, str]:
        """เพิ่ม Transaction ใหม่ลงใน Sheet ประจำวัน (ต่อท้ายเฉพาะคอลัมน์ A:H)"""
        try:
            spreadsheet = self._get_dynamic_spreadsheet()
            now = datetime.now()
            sheet_name = now.strftime("%d")

            # ดึง Worksheet ประจำวัน หรือสร้างใหม่ถ้ายังไม่มี
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                logger.info(f"📄 กำลังสร้างชีทสำหรับวันที่ {sheet_name}...")
                worksheet = spreadsheet.add_worksheet(
                    title=sheet_name, rows=1000, cols=20
                )

                # สร้าง Header ให้ชีทใหม่
                headers = [
                    "Trans ID", "VIP WE รับ", "Time", "Agent",
                    "Trans ID", "VIP 12 รับ P", "Time", "Agent"
                ]
                worksheet.update("A1:H1", [headers])

                # ลบ Sheet1 ตั้งต้นออก (ถ้ามี)
                try:
                    sheet1 = spreadsheet.worksheet("Sheet1")
                    spreadsheet.del_worksheet(sheet1)
                except Exception:
                    pass

            # จัดฟอร์แมตเวลาให้แสดงแบบ HH:mm (เช่น 23:03 หรือ 0:06 ตามรูปเป้าหมาย)
            formatted_time = now.strftime("%H:%M")
            if formatted_time.startswith("0"):
                formatted_time = formatted_time[1:]  # ตัด 0 นำหน้าถ้าเป็นเลขตัวเดียวแบบ 0:06

            user_id = txn.chat_user_id or txn.sender_names or txn.chat_fullname or "-"
            trans_identifier = (
                txn.chat_trans_id
                or txn.chat_user_id
                or txn.sender_names
                or txn.chat_fullname
                or "-"
            )
            
            # แปลงยอดเงินเป็น float เพื่อให้ Google Sheets นำไปจัดรูปแบบตัวเลข #,##0.00 ได้ถูก
            user_amt = self._parse_float(txn.chat_amount)
            trans_amt = self._parse_float(txn.api_total_amount or txn.chat_amount)

            row = [
                # USER
                user_id,
                user_amt if user_amt > 0 else "-",
                formatted_time,
                txn.chat_bank or "-",
                # TRANS
                trans_identifier,
                trans_amt if trans_amt > 0 else "-",
                formatted_time,
                txn.chat_bank or "-",
            ]

            # หาแถวว่างถัดไปเฉพาะคอลัมน์ A
            col_a_values = worksheet.col_values(1)
            next_row = len(col_a_values) + 1

            # เขียนข้อมูลเจาะจงเฉพาะช่วง A{next_row}:H{next_row}
            worksheet.update(f"A{next_row}:H{next_row}", [row])

            # 1. อัปเดต Summary รายวัน
            self.update_daily_summary(worksheet)

            # 2. จัดสไตล์สี / เส้นขอบตาราง
            self._apply_styles(worksheet)

            logger.info(f"✅ บันทึกข้อมูล อัปเดต Summary และใส่ Style สำเร็จ (Row {next_row})")
            return True, ""

        except Exception as e:
            error_msg = f"Google Sheets Error: {e}"
            logger.error(f"⚠️ {error_msg}")
            return False, error_msg


# Singleton Instance หลัก
sheets_service = GoogleSheetsService()


def append_to_sheet(txn) -> Tuple[bool, str]:
    return sheets_service.append_to_sheet(txn)