import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from core.config import config

logger = logging.getLogger(__name__)

class GoogleDriveService:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(GoogleDriveService, cls).__new__(cls)
            cls._instance._init_service()
        return cls._instance

    def _init_service(self):
        """เริ่มต้นการเชื่อมต่อ Google Drive API ด้วย Service Account"""
        scopes = ['https://www.googleapis.com/auth/drive']
        try:
            creds = service_account.Credentials.from_service_account_file(
                config.GOOGLE_CREDENTIALS, 
                scopes=scopes
            )
            self.service = build('drive', 'v3', credentials=creds)
            logger.info("Google Drive Service initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive Service: {e}")
            raise e

    FOLDER_MIME_TYPE = 'application/vnd.google-apps.folder'

    def _find_child_id(self, target_name: str, parent_id: str, mime_type: str = None) -> str:
        """ค้นหาไฟล์/โฟลเดอร์ชื่อที่กำหนด ซึ่งอยู่ใต้ parent_id โดยตรง (ไม่ไล่เข้าโฟลเดอร์ย่อย)"""
        if not parent_id:
            logger.error("Parent folder ID is empty — check DRIVE_FOLDER_ID in the environment.")
            return None

        # ชื่อที่มี ' หรือ \ จะทำให้ query เพี้ยน ต้อง escape ก่อน
        escaped_name = target_name.replace('\\', '\\\\').replace("'", "\\'")
        query = f"'{parent_id}' in parents and name = '{escaped_name}' and trashed = false"
        if mime_type:
            query += f" and mimeType = '{mime_type}'"

        try:
            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, webViewLink)'
            ).execute()

            files = results.get('files', [])

            if files:
                file_obj = files[0]
                file_id = file_obj.get('id')
                logger.info(
                    "Found '%s' | ID: %s | URL: %s",
                    target_name, file_id, file_obj.get('webViewLink'),
                )
                return file_id

            logger.warning("'%s' not found in folder ID: %s", target_name, parent_id)
            return None

        except Exception as e:
            logger.error(f"Failed to look up '{target_name}' in Drive: {e}")
            return None

    def get_folder_id_by_name(self, target_name: str, parent_id: str = None) -> str:
        """ค้นหาโฟลเดอร์ตามชื่อ (เช่น "Deposit-2026") — ไม่ระบุ parent = โฟลเดอร์หลักจาก .env"""
        return self._find_child_id(
            target_name,
            parent_id or config.DRIVE_FOLDER_ID,
            mime_type=self.FOLDER_MIME_TYPE,
        )

    def get_spreadsheet_id_by_name(self, target_name: str, parent_id: str = None) -> str:
        """ค้นหาไฟล์ Spreadsheet ตามชื่อ (เช่น "check_08-2026") แล้วคืน ID

        :param parent_id: โฟลเดอร์ที่ให้ค้นหา ไม่ระบุ = โฟลเดอร์หลักจาก .env
        :return: Spreadsheet ID หรือ None ถ้าไม่พบ
        """
        return self._find_child_id(target_name, parent_id or config.DRIVE_FOLDER_ID)

# สร้าง Object ตัวแทนสำหรับเรียกใช้งานแบบ Singleton
drive_service = GoogleDriveService()