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
        """จัดรูปแบบตารางหลักและ Summary"""

        header_style = {
            "backgroundColor": {
                "red": 1.0,
                "green": 0.95,
                "blue": 0.4,
            },
            "textFormat": {
                "bold": True,
                "foregroundColor": {
                    "red": 0.0,
                    "green": 0.0,
                    "blue": 0.0,
                },
                "fontSize": 10,
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        }

        sub_header_style = {
            "backgroundColor": {
                "red": 0.95,
                "green": 0.95,
                "blue": 0.95,
            },
            "textFormat": {
                "bold": True,
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
        }

        border_style = {
            "borders": {
                "top": {"style": "SOLID"},
                "bottom": {"style": "SOLID"},
                "left": {"style": "SOLID"},
                "right": {"style": "SOLID"},
                "innerHorizontal": {"style": "SOLID"},
                "innerVertical": {"style": "SOLID"},
            }
        }

        try:
            last_row = len(worksheet.get_all_values())

            # ======================
            # ตารางหลัก A:H
            # ======================
            worksheet.format("A1:H1", header_style)

            worksheet.format(
                "C2:C1000",
                {
                    "textFormat": {
                        "foregroundColor": {
                            "red": 0.5,
                            "green": 0.0,
                            "blue": 0.5,
                        },
                        "bold": True,
                    },
                    "horizontalAlignment": "CENTER",
                },
            )

            worksheet.format(
                "G2:G1000",
                {
                    "textFormat": {
                        "foregroundColor": {
                            "red": 0.5,
                            "green": 0.0,
                            "blue": 0.5,
                        },
                        "bold": True,
                    },
                    "horizontalAlignment": "CENTER",
                },
            )

            worksheet.format(f"A1:H{last_row}", border_style)

            # ======================
            # Summary L:N
            # ======================
            worksheet.format("L1:N1", header_style)
            worksheet.format("L4:N4", sub_header_style)
            worksheet.format("L8:N8", sub_header_style)
            worksheet.format("L12:N13", sub_header_style)

            worksheet.format("L1:N1000", border_style)

        except Exception as e:
            logger.warning(f"Formatting failed: {e}")

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
            # ป้องกัน IndexError โดยดึงค่าแบบปลอดภัยด้วย Index
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

                # เก็บสถิติลูกค้า
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
        """เพิ่ม Transaction ใหม่ลงใน Sheet ประจำวัน (ใช้ Header 8 คอลัมน์เดิม)"""
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

                # สร้าง Header ให้ชีทใหม่ตามที่คุณกำหนด
                headers = [
                    "Trans ID", "VIP WE รับ", "Time", "Agent",
                    "Trans ID", "VIP 12 รับ P", "Time", "Agent"
                ]
                worksheet.append_row(headers)

                # ลบ Sheet1 ตั้งต้นออก (ถ้ามี)
                try:
                    sheet1 = spreadsheet.worksheet("Sheet1")
                    spreadsheet.del_worksheet(sheet1)
                except Exception:
                    pass

            time_str = now.strftime("%H:%M:%S")

            # กำหนดข้อมูลที่จะนำลง Sheet
            user_id = txn.chat_user_id or txn.sender_names or txn.chat_fullname or "-"
            
            trans_identifier = (
                txn.chat_trans_id
                or txn.chat_user_id
                or txn.sender_names
                or txn.chat_fullname
                or "-"
            )
            trans_amount = txn.api_total_amount or txn.chat_amount or "-"

            row = [
                # USER
                user_id,
                txn.chat_amount or "-",
                time_str,
                txn.chat_bank or "-",
                # TRANS
                trans_identifier,
                trans_amount,
                time_str,
                txn.chat_bank or "-",
            ]

            worksheet.append_row(row)

            # อัปเดต สรุปยอดรายวัน
            self.update_daily_summary(worksheet)

            logger.info(f"✅ บันทึกข้อมูลและอัปเดต Summary สำเร็จ (วันที่ {sheet_name})")
            return True, ""

        except Exception as e:
            error_msg = f"Google Sheets Error: {e}"
            logger.error(f"⚠️ {error_msg}")
            return False, error_msg


# Singleton Instance หลัก
sheets_service = GoogleSheetsService()


def append_to_sheet(txn) -> Tuple[bool, str]:
    return sheets_service.append_to_sheet(txn)