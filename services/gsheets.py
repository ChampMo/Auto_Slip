from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import threading
from typing import Tuple, Any
from zoneinfo import ZoneInfo

from core.config import config
from core.names import get_first_names
from google.oauth2.service_account import Credentials
import gspread
from services.gdrive import DriveUnavailable, drive_service
from services.easyslip import BANK_DROPDOWN_VALUES

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# เวลาสองแบบ ใช้คนละหน้าที่ อย่าสลับกัน
#
#   BUSINESS_TZ — ใช้ตัดสินว่าสลิปใบหนึ่งเป็นของ "วันไหน เดือนไหน"
#   BANGKOK_TZ  — ใช้แสดงเวลาให้คนอ่านเท่านั้น (ตรงกับนาฬิกาบนกำแพง)
#
# วันทางธุรกิจเริ่ม 23:00 ตามเวลาไทย ซึ่งเท่ากับเที่ยงคืนของ UTC+8 พอดี
# จึงใช้ offset +8 เป็นตัวตัดวัน แทนการไปลบชั่วโมงทีละจุด
# ทำแบบนี้แล้วงานสิ้นเดือน (day="last") จะนับวันสุดท้ายตามปฏิทินธุรกิจให้เอง
#
# ใช้ offset คงที่ ไม่ใช่ ZoneInfo ของประเทศใดประเทศหนึ่ง เพราะ
# เราหมายถึง "วันของเราเริ่ม 5 ทุ่ม" ไม่ได้หมายถึงเขตเวลาของประเทศนั้นจริงๆ
# และถ้าประเทศนั้นเปลี่ยนกฎเวลาในอนาคต ระบบจะได้ไม่เลื่อนตามโดยไม่ตั้งใจ
# ─────────────────────────────────────────────────────────────────────────────
BUSINESS_TZ = timezone(timedelta(hours=8), "UTC+8")
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")

# offset คงที่ของเวลาไทย ใช้ตอนประกอบเวลาที่แอดมินพิมพ์เอง
# (ไทยไม่มี DST ค่านี้จึงเท่ากับ BANGKOK_TZ เสมอ แต่เขียนชัดกว่า)
THAI_TZ = timezone(timedelta(hours=7), "UTC+7")

# ชั่วโมงที่วันธุรกิจเริ่ม ตามเวลาไทย (ไว้อ้างอิงในข้อความและเอกสาร)
BUSINESS_DAY_STARTS_AT = "23:00"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_business_now() -> datetime:
    """เวลาปัจจุบันตามปฏิทินธุรกิจ — ใช้ตัดสินว่าสลิปเป็นของวันไหน"""
    return datetime.now(BUSINESS_TZ)


def to_bangkok_clock(dt: datetime) -> datetime:
    """แปลงเป็นเวลานาฬิกาไทย ใช้ตอนแสดงให้คนอ่านเท่านั้น"""
    return dt.astimezone(BANGKOK_TZ)


def format_clock(dt: datetime) -> str:
    """เวลาแบบที่ใช้ในช่อง Time ของชีท เช่น 0:18 หรือ 19:04 (ตัดศูนย์นำหน้าออก)

    แสดงตามนาฬิกาไทยเสมอ เพราะเป็นเวลาที่คนอ่านชีทคาดหวัง
    """
    thai = to_bangkok_clock(dt) if dt.tzinfo is not None else dt
    return f"{thai.hour}:{thai.minute:02d}"


def format_transfer_times(moments) -> str:
    """เวลาโอนของทุกใบในชุด ต่อกันด้วย , ตามลำดับเวลา เช่น '0:18, 0:37'

    รูปเดียวส่งได้หลายสลิป ชีทจึงต้องเห็นครบทุกใบ ไม่ใช่แค่ใบแรก
    """
    valid = sorted(m for m in (moments or []) if m is not None)
    return ", ".join(format_clock(moment) for moment in valid)


def get_sheet_name_for_datetime(dt: datetime) -> str:
    """ชื่อแท็บรายวัน เช่น 04-08-2026"""
    return dt.astimezone(BUSINESS_TZ).strftime("%d-%m-%Y")


def get_year_folder_name(dt: datetime) -> str:
    """ชื่อโฟลเดอร์รายปีที่อยู่ใต้โฟลเดอร์หลัก เช่น Deposit-2026"""
    return dt.astimezone(BUSINESS_TZ).strftime("Deposit-%Y")


def get_month_file_name(dt: datetime) -> str:
    """ชื่อไฟล์รายเดือนที่อยู่ในโฟลเดอร์รายปี เช่น check_08-2026"""
    return dt.astimezone(BUSINESS_TZ).strftime("check_%m-%Y")


def describe_expected_path(dt: datetime) -> str:
    """เส้นทางเต็มที่ควรจะเป็น ใช้บอกคนอ่าน log/ข้อความแจ้งเตือน"""
    return f"{get_year_folder_name(dt)}/{get_month_file_name(dt)}"


@dataclass(frozen=True)
class SheetEntry:
    """ข้อมูลเท่าที่ต้องใช้เขียนลงชีท

    แยกออกมาจาก Transaction ของ SQLAlchemy เพราะการเขียนชีทรันคนละเธรดกับ bot
    ถ้าส่ง ORM object ข้ามเธรดไป การอ่าน attribute อาจไปเรียก session ของอีกเธรดโดยไม่ตั้งใจ
    """
    batch_id: str
    category: str
    status: str
    chat_trans_id: str | None
    chat_fullname: str | None
    sender_names: str | None
    chat_amount: float | None
    api_total_amount: float | None
    receiver_account: str | None
    chat_bank: str | None
    # เวลาโอนตามสลิป พร้อมเขียนลงช่อง Time ได้เลย เช่น "0:18" หรือ "0:18, 0:37"
    # ว่าง = ไม่รู้เวลาโอน ให้ใช้เวลาที่บันทึกแทนเพื่อไม่ให้ช่องโล่ง
    transfer_time_text: str | None = None

    @classmethod
    def from_transaction(cls, txn) -> "SheetEntry":
        return cls(
            transfer_time_text=txn.transfer_time_text,
            batch_id=txn.batch_id,
            category=txn.category,
            status=txn.status,
            chat_trans_id=txn.chat_trans_id,
            chat_fullname=txn.chat_fullname,
            sender_names=txn.sender_names,
            chat_amount=txn.chat_amount,
            api_total_amount=txn.api_total_amount,
            receiver_account=txn.receiver_account,
            chat_bank=txn.chat_bank,
        )


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
            # bot เขียนสลิปจาก worker thread ส่วน scheduler สร้างชีทประจำวันจากอีกเธรด
            # ทั้งสองใช้ service ตัวเดียวกัน จึงต้องเข้าคิวกันไม่ให้คำนวณแถวถัดไปทับกัน
            # (RLock เพราะ append_to_sheet อาจเรียก create_today_sheet ต่อในเธรดเดียวกัน)
            self._write_lock = threading.RLock()
            logger.info("Google Sheets Service initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets Service: {e}")
            raise e

    def _get_dynamic_spreadsheet(self, target: datetime | None = None) -> gspread.Spreadsheet:
        """ค้นหาไฟล์ Spreadsheet ประจำเดือนผ่าน Drive — ค้นใหม่ทุกครั้ง ไม่เก็บ ID ไว้ข้ามการเรียก

        ตั้งใจไม่ cache เพราะถ้าไฟล์ถูกลบแล้วสร้างใหม่ในชื่อเดิม ID เก่าจะยังเปิดได้
        (Google เปิดไฟล์ที่อยู่ในถังขยะผ่าน ID ได้) แล้วข้อมูลจะไปลงไฟล์ที่ถูกลบแบบเงียบๆ
        การค้นใหม่ทุกครั้งกรอง trashed = false อยู่แล้ว จึงไม่มีทางเขียนลงไฟล์ที่ถูกลบ

        target คือวันที่ที่ต้องการ ไม่ใช่ 'วันนี้' เสมอไป — คืนวันสิ้นเดือนเราสร้างแท็บของ
        วันพรุ่งนี้ ซึ่งต้องไปลงไฟล์ของเดือนถัดไป
        """
        now = target or get_business_now()
        folder_name = get_year_folder_name(now)
        file_name = get_month_file_name(now)

        logger.debug("Resolving sheet file: %s", describe_expected_path(now))

        # ชั้นที่ 1: โฟลเดอร์รายปีใต้โฟลเดอร์หลัก
        folder_id = drive_service.get_folder_id_by_name(folder_name)
        if not folder_id:
            raise gspread.exceptions.SpreadsheetNotFound(
                f"ไม่พบโฟลเดอร์ '{folder_name}' ในโฟลเดอร์หลักของ Google Drive"
            )

        # ชั้นที่ 2: ไฟล์รายเดือนในโฟลเดอร์รายปี
        sheet_id = drive_service.get_spreadsheet_id_by_name(file_name, folder_id)
        if not sheet_id:
            raise gspread.exceptions.SpreadsheetNotFound(
                f"ไม่พบไฟล์ '{file_name}' ในโฟลเดอร์ '{folder_name}'"
            )

        return self.client.open_by_key(sheet_id)

    @staticmethod
    def _parse_float(val: Any) -> float:
        """Helper แปลงค่าใน Cell เป็น float อย่างปลอดภัย"""
        try:
            if isinstance(val, str):
                val = val.replace(",", "").strip()
            return float(val)
        except (ValueError, TypeError):
            return 0.0

    AGENT_ACCOUNT_DROPDOWN_VALUES = BANK_DROPDOWN_VALUES

    SUMMARY_ACCOUNT_DROPDOWN_VALUES = ["P", "G", "B", "T", "N", "Y"]
    SUMMARY_BANK_DROPDOWN_VALUES = BANK_DROPDOWN_VALUES

    BASE_CELL_STYLE = {
        "borders": {
            "top": {"style": "SOLID"},
            "bottom": {"style": "SOLID"},
            "left": {"style": "SOLID"},
            "right": {"style": "SOLID"},
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
    }

    def _apply_row_style(self, worksheet: gspread.Worksheet, row_number: int):
        """จัดรูปแบบเฉพาะแถวที่เพิ่งเขียน

        ใช้แทนการจัดสีทั้งตารางทุกครั้ง ซึ่งกิน API หลายสิบครั้งต่อสลิป 1 ใบ
        """
        try:
            worksheet.format(f"A{row_number}:AJ{row_number}", self.BASE_CELL_STYLE)
        except Exception as e:
            logger.error(f"Row style failed (row {row_number}): {e}", exc_info=True)

    def _apply_main_table_style(self, worksheet: gspread.Worksheet):
        """จัดรูปแบบตารางหลัก A:AJ (เรียกตอนสร้างแท็บใหม่เท่านั้น)"""

        try:
            last_row = max(
                len(worksheet.col_values(12)),
                len(worksheet.col_values(16))
            )

            base_style = self.BASE_CELL_STYLE

            if last_row > 0:
                worksheet.format(f"A1:AJ{last_row}", base_style)

            # Header A:S
            worksheet.format(
                "A1:S1",
                {
                    **base_style,
                    "backgroundColor": {
                        "red": 1,
                        "green": 1,
                        "blue": 0.6,
                    },
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {
                            "red": 0,
                            "green": 0,
                            "blue": 0,
                        },
                    },
                },
            )

            # Header T:AJ
            worksheet.format(
                "T1:AJ1",
                {
                    **base_style,
                    "backgroundColor": {
                        "red": 0.2,
                        "green": 0.6,
                        "blue": 1,
                    },
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {
                            "red": 1,
                            "green": 1,
                            "blue": 1,
                        },
                    },
                },
            )

            worksheet.freeze(rows=1)

        except Exception as e:
            logger.error(f"Main table style failed: {e}", exc_info=True)

    def _apply_agent_dropdowns_to_row(self, worksheet: gspread.Worksheet, row_number: int):
        """Set dropdown validation for Agent columns on a newly created row."""
        try:
            sheet_id = getattr(worksheet, "id", None) or worksheet._properties.get("sheetId")
            accounts = [
                {"userEnteredValue": account}
                for account in self.AGENT_ACCOUNT_DROPDOWN_VALUES
            ]

            rule = {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": accounts,
                },
                "strict": True,
                "showCustomUi": True,
            }

            # Agent columns for inserted data rows: N and R (0-based idx 13 and 17)
            dropdown_ranges = [
                {
                    "sheetId": sheet_id,
                    "startRowIndex": row_number - 1,
                    "endRowIndex": row_number,
                    "startColumnIndex": 14,
                    "endColumnIndex": 15,
                },
                {
                    "sheetId": sheet_id,
                    "startRowIndex": row_number - 1,
                    "endRowIndex": row_number,
                    "startColumnIndex": 18,
                    "endColumnIndex": 19,
                },
            ]

            requests = [
                {"setDataValidation": {"range": r, "rule": rule}}
                for r in dropdown_ranges
            ]

            worksheet.spreadsheet.batch_update({"requests": requests})
        except Exception as e:
            logger.error(f"Agent dropdown setup failed: {e}", exc_info=True)

    def _apply_summary_dropdowns(self, worksheet: gspread.Worksheet):
        """Apply dropdown lists to the summary tables for AK, AP, and AU."""
        try:
            sheet_id = getattr(worksheet, "id", None) or worksheet._properties.get("sheetId")
            account_values = [{"userEnteredValue": value} for value in self.SUMMARY_ACCOUNT_DROPDOWN_VALUES]
            bank_values = [{"userEnteredValue": value} for value in self.SUMMARY_BANK_DROPDOWN_VALUES]

            account_rule = {
                "condition": {"type": "ONE_OF_LIST", "values": account_values},
                "strict": True,
                "showCustomUi": True,
            }
            bank_rule = {
                "condition": {"type": "ONE_OF_LIST", "values": bank_values},
                "strict": True,
                "showCustomUi": True,
            }

            requests = [
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "endRowIndex": 8,
                            "startColumnIndex": 36,
                            "endColumnIndex": 37,
                        },
                        "rule": account_rule,
                    }
                },
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "endRowIndex": 17,
                            "startColumnIndex": 41,
                            "endColumnIndex": 42,
                        },
                        "rule": bank_rule,
                    }
                },
                {
                    "setDataValidation": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 2,
                            "endRowIndex": 7,
                            "startColumnIndex": 46,
                            "endColumnIndex": 47,
                        },
                        "rule": bank_rule,
                    }
                },
            ]

            worksheet.spreadsheet.batch_update({"requests": requests})
        except Exception as e:
            logger.error(f"Summary dropdown setup failed: {e}", exc_info=True)

    def _apply_summary_style(self, worksheet: gspread.Worksheet):
        """จัดรูปแบบ Summary Table AK:BC"""

        try:
            border = {
                "top": {"style": "SOLID"},
                "bottom": {"style": "SOLID"},
                "left": {"style": "SOLID"},
                "right": {"style": "SOLID"},
            }

            base = {
                "borders": border,
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            }

            # =========================
            # ตาราง 1 (AK:AO)
            # =========================
            worksheet.format("AK1:AO10", base)

            # Header หลัก: AK-AO สีเขียวอ่อน
            worksheet.format(
                "AK1:AO1",
                {
                    **base,
                    "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85},
                    "textFormat": {"bold": True},
                },
            )

            # Header รอง
            worksheet.format(
                "AK2:AO2",
                {
                    **base,
                    "backgroundColor": {"red": 0.90, "green": 0.97, "blue": 0.90},
                    "textFormat": {"bold": True},
                },
            )

            # Total
            worksheet.format(
                "AK10:AO10",
                {
                    **base,
                    "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85},
                    "textFormat": {"bold": True},
                },
            )

            # =========================
            # ตาราง 2 (AP:AT)
            # =========================
            worksheet.format("AP1:AT19", base)

            worksheet.format(
                "AP1:AT1",
                {
                    **base,
                    "backgroundColor": {"red": 0.90, "green": 0.90, "blue": 0.90},
                    "textFormat": {"bold": True},
                },
            )

            worksheet.format(
                "AP2:AT2",
                {
                    **base,
                    "backgroundColor": {"red": 0.95, "green": 0.95, "blue": 0.95},
                    "textFormat": {"bold": True},
                },
            )

            worksheet.format(
                "AP19:AT19",
                {
                    **base,
                    "backgroundColor": {"red": 0.90, "green": 0.90, "blue": 0.90},
                    "textFormat": {"bold": True},
                },
            )

            # =========================
            # ตาราง 3 (AU:BC)
            # =========================
            worksheet.format("AU1:BC10", base)

            # Header หลัก: AU-AY เป็นสีส้มอ่อน, AZ-BC เป็นสีฟ้า
            worksheet.format(
                "AU1:AY1",
                {
                    **base,
                    "backgroundColor": {
                        "red": 1.00,
                        "green": 0.92,
                        "blue": 0.78,
                    },
                    "textFormat": {"bold": True},
                },
            )
            worksheet.format(
                "AZ1:BC1",
                {
                    **base,
                    "backgroundColor": {
                        "red": 0.80,
                        "green": 0.94,
                        "blue": 0.97,
                    },
                    "textFormat": {"bold": True},
                },
            )

            # Deposit subheader: AU-AY ส้มอ่อน, AZ-BC ฟ้า
            worksheet.format(
                "AU2:AY2",
                {
                    **base,
                    "backgroundColor": {
                        "red": 1.00,
                        "green": 0.96,
                        "blue": 0.88,
                    },
                    "textFormat": {"bold": True},
                },
            )
            worksheet.format(
                "AZ2:BC2",
                {
                    **base,
                    "backgroundColor": {
                        "red": 0.80,
                        "green": 0.94,
                        "blue": 0.97,
                    },
                    "textFormat": {"bold": True},
                },
            )

            # Total row: แยกสีตามตาราง
            worksheet.format(
                "AU10:AY10",
                {
                    **base,
                    "backgroundColor": {
                        "red": 1.00,
                        "green": 0.92,
                        "blue": 0.78,
                    },
                    "textFormat": {"bold": True},
                },
            )
            worksheet.format(
                "AZ10:BC10",
                {
                    **base,
                    "backgroundColor": {
                        "red": 0.80,
                        "green": 0.94,
                        "blue": 0.97,
                    },
                    "textFormat": {"bold": True},
                },
            )

            # Merge table headers so title text centers across each summary block
            worksheet.spreadsheet.batch_update({
                "requests": [
                    {
                        "mergeCells": {
                            "range": {
                                "sheetId": getattr(worksheet, "id", None) or worksheet._properties.get("sheetId"),
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 36,
                                "endColumnIndex": 41,
                            },
                            "mergeType": "MERGE_ALL",
                        }
                    },
                    {
                        "mergeCells": {
                            "range": {
                                "sheetId": getattr(worksheet, "id", None) or worksheet._properties.get("sheetId"),
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 41,
                                "endColumnIndex": 46,
                            },
                            "mergeType": "MERGE_ALL",
                        }
                    },
                    {
                        "mergeCells": {
                            "range": {
                                "sheetId": getattr(worksheet, "id", None) or worksheet._properties.get("sheetId"),
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 46,
                                "endColumnIndex": 51,
                            },
                            "mergeType": "MERGE_ALL",
                        }
                    },
                    {
                        "mergeCells": {
                            "range": {
                                "sheetId": getattr(worksheet, "id", None) or worksheet._properties.get("sheetId"),
                                "startRowIndex": 0,
                                "endRowIndex": 1,
                                "startColumnIndex": 51,
                                "endColumnIndex": 55,
                            },
                            "mergeType": "MERGE_ALL",
                        }
                    },
                ]
            })

        except Exception as e:
            logger.error(f"Summary style failed: {e}", exc_info=True)

    def _write_summary_tables(self, worksheet: gspread.Worksheet):
        """เขียนตารางสรุป — เรียกตอนสร้างแท็บใหม่เท่านั้น

        ทุกช่องเป็นสูตร SUMIF/COUNTIF ที่ Google คำนวณให้เองเมื่อมีข้อมูลเพิ่ม
        จึงไม่ต้องเขียนซ้ำทุกครั้งที่มีสลิปเข้า และของที่แอดมินแก้เองในโซนนี้จะไม่ถูกทับ
        """
        summary = [
            ["สรุปยอดเงินโอนออกทั้งหมด / แยกบัญชี (Withdraw)", "", "", "", ""],
            ["บัญชี", "We88", "12T", "Uwin THB", "Total"],

            ["P", "=SUMIF(C:C,AK3,B:B)", "=SUMIF(G:G,AK3,F:F)", "=SUMIF(J:J,AK3,I:I)", "=SUM(AL3:AN3)"],
            ["G", "=SUMIF(C:C,AK4,B:B)", "=SUMIF(G:G,AK4,F:F)", "=SUMIF(J:J,AK4,I:I)", "=SUM(AL4:AN4)"],
            ["B", "=SUMIF(C:C,AK5,B:B)", "=SUMIF(G:G,AK5,F:F)", "=SUMIF(J:J,AK5,I:I)", "=SUM(AL5:AN5)"],
            ["T", "=SUMIF(C:C,AK6,B:B)", "=SUMIF(G:G,AK6,F:F)", "=SUMIF(J:J,AK6,I:I)", "=SUM(AL6:AN6)"],
            ["N", "=SUMIF(C:C,AK7,B:B)", "=SUMIF(G:G,AK7,F:F)", "=SUMIF(J:J,AK7,I:I)", "=SUM(AL7:AN7)"],
            ["Y", "=SUMIF(C:C,AK8,B:B)", "=SUMIF(G:G,AK8,F:F)", "=SUMIF(J:J,AK8,I:I)", "=SUM(AL8:AN8)"],

            [
                "ยอดที่ไม่มีชื่อ",
                "=B303-SUM(AL3:AL8)",
                "=F303-SUM(AM3:AM8)",
                "=I303-SUM(AN3:AN8)",
                "=SUM(AL9:AN9)"
            ],

            [
                "ยอดเงินออกทั้งหมด",
                "=SUM(AL3:AL9)",
                "=SUM(AM3:AM9)",
                "=SUM(AN3:AN9)",
                "=SUM(AO3:AO9)"
            ],
        ]

        bank_table = [
            ["สรุปยอดเงินโอนออกทั้งหมด / แยกบัญชี (Withdraw)", "", "", "", ""],
            ["Bank", "We88", "12T", "Uwin THB", "Total"],

            ["SCB-CP", "=SUMIF(D:D,AP3,B:B)", "=SUMIF(H:H,AP3,F:F)", "=SUMIF(K:K,AP3,I:I)", "=SUM(AQ3:AS3)"],
            ["KB-CP", "=SUMIF(D:D,AP4,B:B)", "=SUMIF(H:H,AP4,F:F)", "=SUMIF(K:K,AP4,I:I)", "=SUM(AQ4:AS4)"],
            ["BAY-CKB", "=SUMIF(D:D,AP5,B:B)", "=SUMIF(H:H,AP5,F:F)", "=SUMIF(K:K,AP5,I:I)", "=SUM(AQ5:AS5)"],
            ["KB-CKB", "=SUMIF(D:D,AP6,B:B)", "=SUMIF(H:H,AP6,F:F)", "=SUMIF(K:K,AP6,I:I)", "=SUM(AQ6:AS6)"],
            ["KKP-Jak", "=SUMIF(D:D,AP7,B:B)", "=SUMIF(H:H,AP7,F:F)", "=SUMIF(K:K,AP7,I:I)", "=SUM(AQ7:AS7)"],
            ["GSB-Jak", "=SUMIF(D:D,AP8,B:B)", "=SUMIF(H:H,AP8,F:F)", "=SUMIF(K:K,AP8,I:I)", "=SUM(AQ8:AS8)"],
            ["BBL-Ploy", "=SUMIF(D:D,AP9,B:B)", "=SUMIF(H:H,AP9,F:F)", "=SUMIF(K:K,AP9,I:I)", "=SUM(AQ9:AS9)"],
            ["GSB-Ativit", "=SUMIF(D:D,AP10,B:B)", "=SUMIF(H:H,AP10,F:F)", "=SUMIF(K:K,AP10,I:I)", "=SUM(AQ10:AS10)"],
            ["KKP-Yo", "=SUMIF(D:D,AP11,B:B)", "=SUMIF(H:H,AP11,F:F)", "=SUMIF(K:K,AP11,I:I)", "=SUM(AQ11:AS11)"],
            ["TTB-Yo", "=SUMIF(D:D,AP12,B:B)", "=SUMIF(H:H,AP12,F:F)", "=SUMIF(K:K,AP12,I:I)", "=SUM(AQ12:AS12)"],
            ["SCB-Yo", "=SUMIF(D:D,AP13,B:B)", "=SUMIF(H:H,AP13,F:F)", "=SUMIF(K:K,AP13,I:I)", "=SUM(AQ13:AS13)"],
            ["GSB-Yo", "=SUMIF(D:D,AP14,B:B)", "=SUMIF(H:H,AP14,F:F)", "=SUMIF(K:K,AP14,I:I)", "=SUM(AQ14:AS14)"],
            ["KB-CKBกระแส", "=SUMIF(D:D,AP15,B:B)", "=SUMIF(H:H,AP15,F:F)", "=SUMIF(K:K,AP15,I:I)", "=SUM(AQ15:AS15)"],
            ["KB-CPกระแส", "=SUMIF(D:D,AP16,B:B)", "=SUMIF(H:H,AP16,F:F)", "=SUMIF(K:K,AP16,I:I)", "=SUM(AQ16:AS16)"],
            ["KKP-LS", "=SUMIF(D:D,AP17,B:B)", "=SUMIF(H:H,AP17,F:F)", "=SUMIF(K:K,AP17,I:I)", "=SUM(AQ17:AS17)"],
            ["", "=SUMIF(D:D,AP18,B:B)", "=SUMIF(H:H,AP18,F:F)", "=SUMIF(K:K,AP18,I:I)", "=SUM(AQ18:AS18)"],
            ["ถอนเงินออกทั้งหมด", "=SUM(AQ3:AQ18)", "=SUM(AR3:AR18)", "=SUM(AS3:AS18)", "=SUM(AT3:AT18)"],
        ]

        deposit_table = [
            ["สรุปยอดเงินโอนเข้าทั้งหมด / แยกบัญชี (Deposit)", "", "", "", "", "", "", "", ""],
            ["บัญชี", "We88", "12T", "", "Total", "We88", "12T", "", "Total"],
            ["SCB-CP", "=SUMIF(O:O,AU3,M:M)", "=SUMIF(S:S,AU3,Q:Q)", "", "=SUM(AV3:AW3)", "=COUNTIF(O:O,AU3)", "=COUNTIF(S:S,AU3)", "", "=SUM(AZ3:BA3)"],
            ["SCB-MT", "=SUMIF(O:O,AU4,M:M)", "=SUMIF(S:S,AU4,Q:Q)", "", "=SUM(AV4:AW4)", "=COUNTIF(O:O,AU4)", "=COUNTIF(S:S,AU4)", "", "=SUM(AZ4:BA4)"],
            ["GSB-Yo", "=SUMIF(O:O,AU5,M:M)", "=SUMIF(S:S,AU5,Q:Q)", "", "=SUM(AV5:AW5)", "=COUNTIF(O:O,AU5)", "=COUNTIF(S:S,AU5)", "", "=SUM(AZ5:BA5)"],
            ["TTB-Yo", "=SUMIF(O:O,AU6,M:M)", "=SUMIF(S:S,AU6,Q:Q)", "", "=SUM(AV6:AW6)", "=COUNTIF(O:O,AU6)", "=COUNTIF(S:S,AU6)", "", "=SUM(AZ6:BA6)"],
            ["SCB-Yo", "=SUMIF(O:O,AU7,M:M)", "=SUMIF(S:S,AU7,Q:Q)", "", "=SUM(AV7:AW7)", "=COUNTIF(O:O,AU7)", "=COUNTIF(S:S,AU7)", "", "=SUM(AZ7:BA7)"],
            ["", 0, 0, "", 0, 0, 0, "", 0],
            ["", 0, 0, "", 0, 0, 0, "", 0],
            [
                "ยอดเงินเข้าทั้งหมด",
                "=SUM(AV3:AV9)",
                "=SUM(AW3:AW9)",
                "",
                "=SUM(AY3:AY9)",
                "=SUM(AZ3:AZ9)",
                "=SUM(BA3:BA7)",
                "",
                "=SUM(BC3:BC7)"
            ],
        ]

        max_rows = max(len(summary), len(bank_table), len(deposit_table))

        while len(summary) < max_rows:
            summary.append([""] * 5)

        while len(bank_table) < max_rows:
            bank_table.append([""] * 5)

        while len(deposit_table) < max_rows:
            deposit_table.append([""] * 9)

        merged = []
        for i in range(max_rows):
            merged.append(summary[i] + bank_table[i] + deposit_table[i])

        # gspread 6 รับ (values, range_name) — สลับลำดับจากเวอร์ชัน 5
        worksheet.update(
            merged,
            f"AK1:BC{len(merged)}",
            value_input_option="USER_ENTERED",
        )
        self._apply_summary_dropdowns(worksheet)

    def create_sheet_for(self, target: datetime | None = None):
        """สร้างแท็บของวันที่ระบุ ถ้ายังไม่มี (ไม่ระบุ = วันนี้) เข้าคิวร่วมกับการเขียนสลิป"""
        with self._write_lock:
            self._create_sheet_for_locked(target or get_business_now())

    def create_today_sheet(self):
        """สร้างแท็บของวันนี้"""
        self.create_sheet_for()

    def _create_sheet_for_locked(self, target: datetime, spreadsheet=None):
        # เลือกไฟล์รายเดือนตามวันที่เป้าหมาย ไม่ใช่ตามวันที่ปัจจุบัน
        # (ผู้เรียกที่ค้นไฟล์ไว้แล้วส่งต่อมาได้ จะได้ไม่ต้องค้น Drive ซ้ำ)
        if spreadsheet is None:
            spreadsheet = self._get_dynamic_spreadsheet(target)
        sheet_name = get_sheet_name_for_datetime(target)

        logger.info(
            "🕒 Bangkok clock=%s | Business day=%s | Creating sheet=%s",
            to_bangkok_clock(get_business_now()).strftime("%Y-%m-%d %H:%M:%S %Z"),
            get_business_now().strftime("%Y-%m-%d"),
            sheet_name,
        )
        logger.info("💾 Bank dropdown values: %s", BANK_DROPDOWN_VALUES)

        # ถ้ามีชีทแล้ว ไม่ต้องสร้าง
        try:
            spreadsheet.worksheet(sheet_name)
            logger.info(f"📄 Sheet '{sheet_name}' มีอยู่แล้ว")
            return
        except gspread.exceptions.WorksheetNotFound:
            pass

        logger.info(f"📄 กำลังสร้างชีทสำหรับวันที่ {sheet_name}...")
        logger.info("📋 Bank dropdown values to be used: %s", BANK_DROPDOWN_VALUES)

        worksheet = spreadsheet.add_worksheet(
            title=sheet_name,
            rows=1000,
            cols=55
        )

        headers = [
            "Trans ID", "WE88 ออก", "บัญชี", "Bank",
            "Trans ID", "12Play ออก", "บัญชี", "Bank",
            "Uwin ออก", "บัญชี", "Bank",
            "Trans ID", "VIP WE รับ", "Time", "บัญชี",
            "Trans ID", "VIP 12 รับ P", "Time", "บัญชี",
        ]
        
        # เพิ่มค่าธนาคารจาก BANK_DROPDOWN_VALUES
        headers.extend(BANK_DROPDOWN_VALUES)
        logger.info("📊 Sheet headers (after bank values): %s", headers)

        while len(headers) < 55:
            headers.append("")

        worksheet.update([headers], "A1:BC1")
        logger.info("✅ Headers updated to sheet with %d columns", len(headers))

        # ใส่ Style
        self._apply_main_table_style(worksheet)
        self._apply_summary_style(worksheet)

        # สร้าง Summary ตั้งแต่แรก พร้อมกับ Sheet ใหม่
        self._write_summary_tables(worksheet)

        # ลบ Sheet1 (ถ้ามี)
        try:
            sheet1 = spreadsheet.worksheet("Sheet1")
            spreadsheet.del_worksheet(sheet1)
        except Exception:
            pass

        logger.info(f"✅ สร้างชีท '{sheet_name}' สำเร็จ")

    def append_to_sheet(self, entry: SheetEntry) -> Tuple[bool, str]:
        """เพิ่มรายการใหม่ลงใน Sheet ประจำวัน (เขียนเฉพาะ 4 คอลัมน์ของกลุ่มตัวเอง)

        ต้องรับ SheetEntry ไม่ใช่ ORM object เพราะฟังก์ชันนี้ถูกเรียกจาก worker thread
        """
        if str(entry.status).strip().lower() in ("reject", "duplicate"):
            logger.info("Status Reject Skip saving to Google Sheets")
            return True, ""
        # Duplicate write guard is handled by DB audit logs (checked by caller)

        try:
            # กันไม่ให้สองการเขียนคำนวณแถวถัดไปได้เลขเดียวกันแล้วทับกัน
            with self._write_lock:
                now = get_business_now()
                spreadsheet = self._get_dynamic_spreadsheet(now)
                sheet_name = get_sheet_name_for_datetime(now)

                # ดึง Worksheet ประจำวัน หรือสร้างใหม่ถ้ายังไม่มี
                # (ยังถือ lock อยู่ จึงเรียกตัว _locked ตรงๆ ได้)
                try:
                    worksheet = spreadsheet.worksheet(sheet_name)
                except gspread.exceptions.WorksheetNotFound:
                    self._create_sheet_for_locked(now, spreadsheet)
                    worksheet = spreadsheet.worksheet(sheet_name)

                # ช่อง Time = เวลาที่โอนเงินจริงตามสลิป ไม่ใช่เวลาที่กดปุ่มบันทึก
                # สลิปค้างข้ามวันแล้วเพิ่งมากด เวลาที่ลงชีทต้องยังเป็นเวลาที่เงินออกจริง
                # ถ้าอ่านเวลาจากสลิปไม่ได้เลย ค่อยใช้เวลาที่บันทึกแทน ดีกว่าปล่อยช่องว่าง
                formatted_time = (entry.transfer_time_text or "").strip() or format_clock(now)

                # Trans ID: caption -> ชื่อผู้ส่งจากสลิป -> ชื่อผู้ส่งที่แจ้งมาในแชท (กรณีอ่านสลิปไม่ออก)
                trans_identifier = (
                    entry.chat_trans_id
                    or get_first_names(entry.sender_names)
                    or get_first_names(entry.chat_fullname)
                    or ""
                )

                # แปลงยอดเงินเป็น float
                trans_amt = self._parse_float(entry.api_total_amount or entry.chat_amount)
                amt_display = trans_amt if trans_amt > 0 else "-"
                bank_display = entry.receiver_account or entry.chat_bank or "-"

                # 💡 สร้าง Block ข้อมูล 4 คอลัมน์ [Trans ID, ยอดเงิน, Time, บัญชี]
                data_block = [trans_identifier, amt_display, formatted_time, bank_display]

                # 💡 เช็ค Category ว่าเป็นกลุ่มไหน
                category_name = str(entry.category).strip().upper()

                if "12" in category_name or category_name == "VIP_12":
                    # --- กรณีเป็นกลุ่ม VIP 12 ---
                    # นับความลึกเฉพาะคอลัมน์ P (คอลัมน์ที่ 16)
                    col_p_values = worksheet.col_values(16)
                    next_row = len(col_p_values) + 1
                    update_range = f"P{next_row}:S{next_row}"
                else:
                    # --- กรณีเป็นกลุ่ม VIP WE (หรือค่าเริ่มต้น) ---
                    # นับความลึกเฉพาะคอลัมน์ L (คอลัมน์ที่ 12)
                    col_l_values = worksheet.col_values(12)
                    next_row = len(col_l_values) + 1
                    update_range = f"L{next_row}:O{next_row}"

                # 📝 สั่งเขียนข้อมูลลงไปเฉพาะ 4 ช่องของกลุ่มตัวเอง (ไม่ก้าวก่ายฝั่งตรงข้าม)
                # gspread 6 รับ (values, range_name) — สลับลำดับจากเวอร์ชัน 5
                worksheet.update([data_block], update_range)

                # แต่งเฉพาะแถวใหม่ ไม่จัดสี/เขียนตารางสรุปใหม่ทั้งชีท
                # (ตารางสรุปเป็นสูตร Google คำนวณให้เอง — เขียนซ้ำทุกใบเปลืองโควตาเปล่าๆ)
                self._apply_agent_dropdowns_to_row(worksheet, next_row)
                self._apply_row_style(worksheet, next_row)

            logger.info(
                "✅ บันทึกข้อมูลลงชีทสำเร็จ | batch_id=%s | row=%s | range=%s",
                entry.batch_id, next_row, update_range,
            )
            return True, ""

        except DriveUnavailable as exc:
            # แยกให้ชัดว่าติดต่อ Google ไม่ได้ ไม่ใช่ไฟล์หาย
            # ถ้าบอกว่า "ไม่พบไฟล์" คนจะไปสร้างไฟล์ซ้ำทั้งที่ของเดิมยังอยู่
            error_msg = f"Could not reach Google Drive right now: {exc}"
            logger.error("⚠️ %s", error_msg)
            return False, error_msg

        except Exception as e:
            error_msg = f"Google Sheets Error: {e}"
            logger.error(f"⚠️ {error_msg}")
            return False, error_msg


# Singleton Instance หลัก
sheets_service = GoogleSheetsService()


def append_to_sheet(entry: SheetEntry) -> Tuple[bool, str]:
    return sheets_service.append_to_sheet(entry)