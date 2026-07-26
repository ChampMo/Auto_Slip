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
        """จัดรูปแบบทั้งตาราง"""
        
        try:
            last_row = len(worksheet.col_values(19))  # S

            style = {
                "borders": {
                    "top": {"style": "SOLID"},
                    "bottom": {"style": "SOLID"},
                    "left": {"style": "SOLID"},
                    "right": {"style": "SOLID"},
                },
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            }

            # จัดรูปแบบตาราง A:AJ ทั้งหมด
            if last_row > 0:
                worksheet.format(f"A1:AJ{last_row}", style)

            # Header A:S สีเหลือง
            worksheet.format(
                "A1:S1",
                {
                    "backgroundColor": {
                        "red": 1,
                        "green": 1,
                        "blue": 0.6
                    },
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {
                            "red": 0,
                            "green": 0,
                            "blue": 0,
                        },
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                },
            )

            # Header T:AJ สีฟ้า
            worksheet.format(
                "T1:AJ1",
                {
                    "backgroundColor": {
                        "red": 0.2,
                        "green": 0.6,
                        "blue": 1
                    },
                    "textFormat": {
                        "bold": True,
                        "foregroundColor": {
                            "red": 1,
                            "green": 1,
                            "blue": 1,
                        },
                    },
                    "horizontalAlignment": "CENTER",
                    "verticalAlignment": "MIDDLE",
                },
            )

            worksheet.freeze(rows=1)

            logger.info(f"จัดรูปแบบสำเร็จ (A1:AJ{last_row})")

        except Exception as e:
            logger.error(f"Formatting failed: {e}", exc_info=True)

    def update_daily_summary(self, worksheet: gspread.Worksheet):
        
        records = worksheet.get_all_values()
        if len(records) <= 1:
            return

        user_count, user_total = 0, 0.0
        trans_count, trans_total = 0, 0.0
        customers: Dict[str, Dict[str, float]] = {}

        # วนลูปอ่านข้อมูลข้าม Header (Row 1)
        for row in records[1:]:
            # USER (L,M)
            col_user_id = row[11] if len(row) > 11 else ""
            col_user_amt = row[12] if len(row) > 12 else ""

            # TRANS (P,Q)
            col_trans_id = row[15] if len(row) > 15 else ""
            col_trans_amt = row[16] if len(row) > 16 else ""

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

# =========================
# Withdraw Account
# =========================

        summary = [
            ["สรุปยอดเงินโอนออกทั้งหมด / แยกบัญชี (Withdraw)", "", "", "", ""],
            ["บัญชี", "We88", "12T", "Uwin THB", "Total"],

            ["P", "=MOCK_P_WE88", "=MOCK_P_12T", "=MOCK_P_UWIN", "=SUM(AL3:AN3)"],
            ["G", "=MOCK_G_WE88", "=MOCK_G_12T", "=MOCK_G_UWIN", "=SUM(AL4:AN4)"],
            ["B", "=MOCK_B_WE88", "=MOCK_B_12T", "=MOCK_B_UWIN", "=SUM(AL5:AN5)"],
            ["T", "=MOCK_T_WE88", "=MOCK_T_12T", "=MOCK_T_UWIN", "=SUM(AL6:AN6)"],
            ["N", "=MOCK_N_WE88", "=MOCK_N_12T", "=MOCK_N_UWIN", "=SUM(AL7:AN7)"],
            ["Y", "=MOCK_Y_WE88", "=MOCK_Y_12T", "=MOCK_Y_UWIN", "=SUM(AL8:AN8)"],

            [
                "ยอดที่ไม่เข้า",
                "=SUM(AL3:AL8)",
                "=SUM(AM3:AM8)",
                "=SUM(AN3:AN8)",
                "=SUM(AO3:AO8)"
            ],

            [
                "ถอนเงินออกทั้งหมด",
                "=AL9",
                "=AM9",
                "=AN9",
                "=SUM(AL10:AN10)"
            ],
        ]


        # =========================
        # Bank Table
        # =========================

        bank_table = [
            ["สรุปยอดเงินโอนออกทั้งหมด / แยกบัญชี (Withdraw)", "", "", "", ""],
            ["Bank", "We88", "12T", "Uwin THB", "Total"],

            ["SCB-CP", "=M1", "=M2", "=M3", "=SUM(AQ3:AS3)"],
            ["KB-CP", "=M1", "=M2", "=M3", "=SUM(AQ4:AS4)"],
            ["BAY-CKB", "=M1", "=M2", "=M3", "=SUM(AQ5:AS5)"],
            ["KB-CKB", "=M1", "=M2", "=M3", "=SUM(AQ6:AS6)"],
            ["KKP-Jak", "=M1", "=M2", "=M3", "=SUM(AQ7:AS7)"],
            ["GSB-Jak", "=M1", "=M2", "=M3", "=SUM(AQ8:AS8)"],
            ["BBL-Ploy", "=M1", "=M2", "=M3", "=SUM(AQ9:AS9)"],
            ["GSB-Ativit", "=M1", "=M2", "=M3", "=SUM(AQ10:AS10)"],
            ["KKP-Yo", "=M1", "=M2", "=M3", "=SUM(AQ11:AS11)"],
            ["TTB-Yo", "=M1", "=M2", "=M3", "=SUM(AQ12:AS12)"],
            ["SCB-Yo", "=M1", "=M2", "=M3", "=SUM(AQ13:AS13)"],
            ["GSB-Yo", "=M1", "=M2", "=M3", "=SUM(AQ14:AS14)"],
            ["KB-CKทรรศนะ", "=M1", "=M2", "=M3", "=SUM(AQ15:AS15)"],
            ["KB-CPทรรศนะ", "=M1", "=M2", "=M3", "=SUM(AQ16:AS16)"],
            ["KKP-LS", "=M1", "=M2", "=M3", "=SUM(AQ17:AS17)"],

            [
                "",
                "=SUM(AQ3:AQ17)",
                "=SUM(AR3:AR17)",
                "=SUM(AS3:AS17)",
                "=SUM(AT3:AT17)"
            ],

            [
                "ถอนเงินออกทั้งหมด",
                "=AQ18",
                "=AR18",
                "=AS18",
                "=SUM(AQ19:AS19)"
            ],
        ]


        # =========================
        # Deposit Table
        # =========================

        deposit_table = [
            ["สรุปยอดเงินโอนเข้าทั้งหมด / แยกบัญชี (Deposit)", "", "", "", "", "", "", "", ""],

            ["บัญชี", "We88", "12T", "", "Total",
            "We88", "12T", "", "Total"],

            ["SCB-CP", "=M1", "=M2", "", "=SUM(AV3:AW3)", 0, 0, "", "=SUM(AZ3:BA3)"],
            ["SCB-MT", "=M1", "=M2", "", "=SUM(AV4:AW4)", 0, 0, "", "=SUM(AZ4:BA4)"],
            ["GSB-Yo", "=M1", "=M2", "", "=SUM(AV5:AW5)", 0, 0, "", "=SUM(AZ5:BA5)"],
            ["TTB-Yo", "=M1", "=M2", "", "=SUM(AV6:AW6)", 0, 0, "", "=SUM(AZ6:BA6)"],
            ["SCB-Yo", "=M1", "=M2", "", "=SUM(AV7:AW7)", 0, 0, "", "=SUM(AZ7:BA7)"],

            ["",0,0,"",0,0,0,"",0],
            ["",0,0,"",0,0,0,"",0],

            [
                "ยอดเงินเข้าทั้งหมด",
                "=SUM(AV3:AV9)",
                "=SUM(AW3:AW9)",
                "",
                "=SUM(AX3:AX9)",
                "=SUM(AY3:AY9)",
                "=SUM(AZ3:AZ9)",
                "",
                "=SUM(BC3:BC9)"
            ],
        ]

        
        for name, data in customers.items():
            if data["count"] >= 3:
                summary.append([name, data["count"], data["total"]])

        # เขียนข้อมูลกลับไปยัง Column L1:N
        summary = [row + [""] * (3 - len(row)) for row in summary]
        end_row = len(summary)
        max_rows = max(
            len(summary),
            len(bank_table),
            len(deposit_table)
        )

        while len(summary) < max_rows:
            summary.append([""] * 5)

        while len(bank_table) < max_rows:
            bank_table.append([""] * 5)

        while len(deposit_table) < max_rows:
            deposit_table.append([""] * 9)


        merged = []

        for i in range(max_rows):
            merged.append(
                summary[i]
                + bank_table[i]
                + deposit_table[i]
            )


        worksheet.update(
            f"AK1:BC{len(merged)}",
            merged,
            value_input_option="USER_ENTERED"
        )

    def append_to_sheet(self, txn) -> Tuple[bool, str]:
        """เพิ่ม Transaction ใหม่ลงใน Sheet ประจำวัน (ต่อท้ายเฉพาะคอลัมน์ A:H)"""
        if str(txn.status).strip().lower() == "reject":
            logger.info("สถานะ Reject ข้ามการบันทึกลง Google Sheets")
            return True, ""
        
        try:
            spreadsheet = self._get_dynamic_spreadsheet()
            now = datetime.now()
            sheet_name = now.strftime("%d-%m-%Y")

            # ดึง Worksheet ประจำวัน หรือสร้างใหม่ถ้ายังไม่มี
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
            except gspread.exceptions.WorksheetNotFound:
                logger.info(f"📄 กำลังสร้างชีทสำหรับวันที่ {sheet_name}...")
                worksheet = spreadsheet.add_worksheet(
                    title=sheet_name,
                    rows=1000,
                    cols=40
                )

                # สร้าง Header ให้ชีทใหม่
                headers = [
                    "Trans ID", "WE88 ออก", "Agent", "Bank",
                    "Trans ID", "12Play ออก", "Agent", "Bank",
                    "Uwin ออก", "Agent", "Bank",
                    "Trans ID", "VIP WE รับ", "Time", "Agent",
                    "Trans ID", "VIP 12 รับ P", "Time", "Agent",

                    "KB-CP",
                    "KB-CPกระแส",
                    "BAY-CKB",
                    "KB-CKB",
                    "KB-CKBกระแส",
                    "KKP-Jak",
                    "GSB-Jak",
                    "BBL-Ploy",
                    "GSB-Ativit",
                    "KKP-LS",
                    "SCB-CP",
                    "GSB-Yo",
                    "TTB-Yo",
                    "SCB-Yo",
                    "SCB-MT",
                    "Cash ตา",
                    "Cash Bas"
                ]
                worksheet.update("A1:AK1", [headers])

                self._apply_styles(worksheet)

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

            # หาแถวว่างถัดไปเฉพาะคอลัมน์ L
            col_l_values = worksheet.col_values(12)
            next_row = len(col_l_values) + 1

            # เขียนข้อมูลเจาะจงเฉพาะช่วง L{next_row}:S{next_row}
            worksheet.update(f"L{next_row}:S{next_row}", [row])

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