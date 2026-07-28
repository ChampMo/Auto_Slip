from datetime import datetime
import logging
from typing import Tuple, Any, Dict
from wsgiref import headers

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

    AGENT_ACCOUNT_DROPDOWN_VALUES = [
        "SCB-CP",
        "KB-CP",
        "BAY-CKB",
        "KB-CKB",
        "KKP-Jak",
        "GSB-Jak",
        "BBL-Ploy",
        "GSB-Ativit",
        "KKP-Yo",
        "TTB-Yo",
        "SCB-Yo",
        "GSB-Yo",
        "SCB-MT",
        "KKP-LS",
        "KB-CPทรรศนะ",
        "KB-CKBกระแส",
    ]

    SUMMARY_ACCOUNT_DROPDOWN_VALUES = ["P", "G", "B", "T", "N", "Y"]
    SUMMARY_BANK_DROPDOWN_VALUES = [
        "SCB-CP",
        "KB-CP",
        "BAY-CKB",
        "KB-CKB",
        "KKP-Jak",
        "GSB-Jak",
        "BBL-Ploy",
        "GSB-Ativit",
        "KKP-Yo",
        "TTB-Yo",
        "SCB-Yo",
        "GSB-Yo",
        "KB-CKBกระแส",
        "KB-CKกระแส",
        "KKP-LS",
    ]

    def _apply_main_table_style(self, worksheet: gspread.Worksheet):
        """จัดรูปแบบตารางหลัก A:AJ"""

        try:
            last_row = max(
                len(worksheet.col_values(12)),
                len(worksheet.col_values(16))
            )

            base_style = {
                "borders": {
                    "top": {"style": "SOLID"},
                    "bottom": {"style": "SOLID"},
                    "left": {"style": "SOLID"},
                    "right": {"style": "SOLID"},
                },
                "horizontalAlignment": "CENTER",
                "verticalAlignment": "MIDDLE",
            }

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

    def update_daily_summary(self, worksheet: gspread.Worksheet):
        records = worksheet.get_all_values()
        if len(records) <= 1:
            return

        customers: Dict[str, Dict[str, float]] = {}

        # วนลูปอ่านข้อมูลข้าม Header (Row 1)
        for row in records[1:]:
            # อ่านข้อมูลกลุ่ม VIP WE (L, M)
            col_we_id = row[11] if len(row) > 11 else ""
            col_we_amt = row[12] if len(row) > 12 else ""

            # อ่านข้อมูลกลุ่ม VIP 12 (P, Q)
            col_12_id = row[15] if len(row) > 15 else ""
            col_12_amt = row[16] if len(row) > 16 else ""

            # 💡 ดึง ID และ ยอดเงิน มาจากช่องที่มีข้อมูล (เนื่องจากมันจะถูกเติมแค่ฝั่งใดฝั่งหนึ่ง)
            active_id = col_we_id if str(col_we_id).strip() else col_12_id
            active_amt = col_we_amt if str(col_we_id).strip() else col_12_amt

            if active_id and str(active_id).strip() != "-":
                amt = self._parse_float(active_amt)

                if active_id not in customers:
                    customers[active_id] = {"count": 0, "total": 0.0}

                customers[active_id]["count"] += 1
                customers[active_id]["total"] += amt

        # จัดโครงสร้างตาราง Summary

# =========================
# Withdraw Account
# =========================

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


        # =========================
        # Bank Table
        # =========================

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
            ["GSB-Ativit", "=SUMIF(D:D,AP10,B:B)", "=SUMIF(H:H,AP10,F:F)", "=SUMIF(K:K,AP10,I:I)", "= SUM(AQ10:AS10)"],
            ["KKP-Yo", "= SUMIF(D:D,AP11,B:B)", "= SUMIF(H:H,AP11,F:F)", "= SUMIF(K:K,AP11,I:I)", "= SUM(AQ11:AS11)"],
            ["TTB-Yo", "= SUMIF(D:D,AP12,B:B)", "= SUMIF(H:H,AP12,F:F)", "= SUMIF(K:K,AP12,I:I)", "= SUM(AQ12:AS12)"],
            ["SCB-Yo", "= SUMIF(D:D,AP13,B:B)", "= SUMIF(H:H,AP13,F:F)", "= SUMIF(K:K,AP13,I:I)","= SUM(AQ13:AS13)"],
            ["GSB-Yo", "=SUMIF(D:D,AP14,B:B)", "=SUMIF(H:H,AP14,F:F)", "=SUMIF(K:K,AP14,I:I)", "=SUM(AQ14:AS14)"],
            ["KB-CKทรรศนะ", "=SUMIF(D:D,AP15,B:B)", "=SUMIF(H:H,AP15,F:F)", "=SUMIF(K:K,AP15,I:I)", "=SUM(AQ15:AS15)"],
            ["KB-CPทรรศนะ", "=SUMIF(D:D,AP16,B:B)", "=SUMIF(H:H,AP16,F:F)", "=SUMIF(K:K,AP16,I:I)", "=SUM(AQ16:AS16)"],
            ["KKP-LS", "=SUMIF(D:D,AP17,B:B)", "=SUMIF(H:H,AP17,F:F)", "=SUMIF(K:K,AP17,I:I)", "=SUM(AQ17:AS17)"],

            [
                "",
                "=SUMIF(D:D,AP18,B:B)",
                "=SUMIF(H:H,AP18,F:F)",
                "=SUMIF(K:K,AP18,I:I)",
                "=SUM(AQ18:AS18)"
            ],

            [
                "ถอนเงินออกทั้งหมด",
                "=SUM(AQ3:AQ18)",
                "=SUM(AR3:AR18)",
                "=SUM(AS3:AS18)",
                "=SUM(AT3:AT18)"
            ],
            
        ]


        # =========================
        # Deposit Table
        # =========================

        deposit_table = [
            ["สรุปยอดเงินโอนเข้าทั้งหมด / แยกบัญชี (Deposit)", "", "", "", "", "", "", "", ""],

            ["บัญชี", "We88", "12T", "", "Total",
            "We88", "12T", "", "Total"],

            ["SCB-CP", "=SUMIF(O:O,AU3,M:M)", "=SUMIF(S:S,AU3,Q:Q)", "", "=SUM(AV3:AW3)", "=COUNTIF(O:O,AU3)", "=COUNTIF(S:S,AU3)", "", "=SUM(AZ3:BA3)"],
            ["SCB-MT", "=SUMIF(O:O,AU4,M:M)", "=SUMIF(S:S,AU4,Q:Q)", "", "=SUM(AV4:AW4)", "=COUNTIF(O:O,AU4)", "=COUNTIF(S:S,AU4)", "", "=SUM(AV4:AW4)"],
            ["GSB-Yo", "=SUMIF(O:O,AU5,M:M)", "=SUMIF(S:S,AU5,Q:Q)", "", "=SUM(AV5:AW5)", "=COUNTIF(O:O,AU5)", "=COUNTIF(S:S,AU5)", "", "=SUM(AV5:AW5)"],
            ["TTB-Yo", "=SUMIF(O:O,AU6,M:M)", "=SUMIF(S:S,AU6,Q:Q)", "", "=SUM(AV6:AW6)", "=COUNTIF(O:O,AU6)", "=COUNTIF(S:S,AU6)", "", "=SUM(AZ6:BA6)"],
            ["SCB-Yo", "=SUMIF(O:O,AU7,M:M)", "=SUMIF(S:S,AU7,Q:Q)", "", "=SUM(AV7:AW7)", "=COUNTIF(O:O,AU7)", "=COUNTIF(S:S,AU7)", "", "=SUM(AZ7:BA7)"],

            ["",0,0,"",0,0,0,"",0],
            ["",0,0,"",0,0,0,"",0],

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
        self._apply_summary_dropdowns(worksheet)

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
                    cols=55
                )

                # สร้าง Header ให้ชีทใหม่
                headers = [
                    "Trans ID", "WE88 ออก", "บัญชี", "Bank",
                    "Trans ID", "12Play ออก", "บัญชี", "Bank",
                    "Uwin ออก", "บัญชี", "Bank",
                    "Trans ID", "VIP WE รับ", "Time", "บัญชี",
                    "Trans ID", "VIP 12 รับ P", "Time", "บัญชี",

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

                while len(headers) < 55:
                    headers.append("")
                worksheet.update("A1:BC1", [headers])

                self._apply_main_table_style(worksheet)
                self._apply_summary_style(worksheet)


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

            # ดึงข้อมูล Trans ID
            trans_identifier = (
                txn.chat_trans_id
                or txn.chat_user_id
                or txn.sender_names
                or txn.chat_fullname
                or "-"
            )
            
            # แปลงยอดเงินเป็น float
            trans_amt = self._parse_float(txn.api_total_amount or txn.chat_amount)
            amt_display = trans_amt if trans_amt > 0 else "-"
            bank_display = txn.chat_bank or "-"
            
            # 💡 สร้าง Block ข้อมูล 4 คอลัมน์ [Trans ID, ยอดเงิน, Time, บัญชี]
            data_block = [trans_identifier, amt_display, formatted_time, bank_display]

            # 💡 เช็ค Category จาก DB ว่าเป็นกลุ่มไหน
            category_name = str(txn.category).strip().upper()
            
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
            worksheet.update(update_range, [data_block])
            
            # สร้าง Dropdown ให้กับบรรทัดใหม่
            self._apply_agent_dropdowns_to_row(worksheet, next_row)

            # 1. อัปเดต Summary รายวัน
            self.update_daily_summary(worksheet)

            self._apply_main_table_style(worksheet)
            
            self._apply_summary_style(worksheet)

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