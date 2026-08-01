import logging
from datetime import datetime
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

from core.config import config
from database.models import Transaction
from database.session import SessionLocal
from services.gdrive import drive_service

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def build_today_sheet_name() -> str:
    """Return the tab name for today in the format Slip_DD-MM-YYYY."""
    return f"Slip_{datetime.now().strftime('%d-%m-%Y')}"


class GoogleSheetsService:
    """Service layer for Google Sheets operations."""

    def __init__(self) -> None:
        self.client: Optional[gspread.Client] = None
        self._spreadsheet_id = config.GOOGLE_SHEET_ID
        self._credential_path = config.GOOGLE_CREDENTIAL_PATH or config.GOOGLE_CREDENTIALS
        self._init_client()

    def _resolve_spreadsheet_id(self) -> str:
        """Resolve the spreadsheet ID from config or from Google Drive by folder/name."""
        if self._spreadsheet_id:
            return self._spreadsheet_id

        today = datetime.now().strftime("%m-%Y")
        fallback_name = f"Slips_{today}"
        logger.info("GOOGLE_SHEET_ID not configured, trying to resolve from Drive: %s", fallback_name)
        resolved_id = drive_service.get_spreadsheet_id_by_name(fallback_name)
        if resolved_id:
            self._spreadsheet_id = resolved_id
            return resolved_id

        raise ValueError("GOOGLE_SHEET_ID is not configured and no spreadsheet was found in Google Drive")

    def _init_client(self) -> None:
        try:
            if not self._credential_path:
                raise ValueError("GOOGLE_CREDENTIAL_PATH or GOOGLE_CREDENTIALS is not configured")

            credentials = Credentials.from_service_account_file(self._credential_path, scopes=SCOPES)
            self.client = gspread.authorize(credentials)
            logger.info("Google Sheets client initialized successfully")
        except Exception as exc:
            logger.exception("Failed to initialize Google Sheets client: %s", exc)
            raise

    def create_today_sheet(self) -> None:
        """Create a new worksheet for today if it does not already exist and populate it from DB."""
        try:
            if self.client is None:
                raise RuntimeError("Google Sheets client is not initialized")

            sheet_name = build_today_sheet_name()
            spreadsheet_id = self._resolve_spreadsheet_id()
            spreadsheet = self.client.open_by_key(spreadsheet_id)
            existing_worksheets = [ws.title for ws in spreadsheet.worksheets()]

            if sheet_name in existing_worksheets:
                logger.info("Worksheet already exists: %s", sheet_name)
                worksheet = spreadsheet.worksheet(sheet_name)
            else:
                worksheet = spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=30)
                logger.info("Created worksheet: %s", sheet_name)
                self._add_header(worksheet)
                logger.info("Header added to worksheet: %s", sheet_name)

            self._write_transactions_to_sheet(worksheet)
        except Exception as exc:
            logger.exception("Failed to create today's sheet: %s", exc)
            raise

    def _add_header(self, worksheet: gspread.Worksheet) -> None:
        """Add the full header row and base formatting used by the legacy sheet flow."""
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

    def _write_transactions_to_sheet(self, worksheet: gspread.Worksheet) -> None:
        """Read transactions from the database and append them to the worksheet."""
        try:
            with SessionLocal() as db:
                transactions = (
                    db.query(Transaction)
                    .order_by(Transaction.created_at.asc())
                    .all()
                )

            if not transactions:
                logger.info("No transactions found in database; nothing to write to sheets")
                return

            rows = []
            for index, txn in enumerate(transactions, start=1):
                rows.append([
                    index,
                    txn.created_at.strftime("%Y-%m-%d %H:%M:%S") if txn.created_at else "",
                    txn.category or "",
                    txn.receiver_account or txn.chat_bank or "",
                    txn.api_total_amount if txn.api_total_amount is not None else txn.chat_amount or 0,
                    txn.status or "",
                    txn.raw_user_caption or "",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ])

            if rows:
                worksheet.append_rows(rows, value_input_option="USER_ENTERED")
                logger.info("Wrote %s transactions to Google Sheet", len(rows))
        except Exception as exc:
            logger.exception("Failed to write transactions to Google Sheet: %s", exc)
            raise


def create_today_sheet() -> None:
    """Convenience wrapper for scheduler and bot usage."""
    service = GoogleSheetsService()
    service.create_today_sheet()
