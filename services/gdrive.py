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

    def get_spreadsheet_id_by_name(self, target_name: str) -> str:
        """
        ค้นหาไฟล์ใน DRIVE_FOLDER_ID ตามชื่อที่กำหนด แล้วดึง Spreadsheet ID ออกมาใช้งาน
        :param target_name: ชื่อไฟล์ที่ต้องการค้นหา (เช่น "Slips_07-2026")
        :return: Spreadsheet ID หรือ None ถ้าไม่พบ
        """
        parent_id = config.DRIVE_FOLDER_ID
        if not parent_id:
            logger.error("DRIVE_FOLDER_ID is not set in the configuration/environment.")
            return None

        try:
            # ค้นหาไฟล์ในโฟลเดอร์ที่ตรงกับชื่อและไม่ใช่ไฟล์ที่ถูกลบ
            query = f"'{parent_id}' in parents and name = '{target_name}' and trashed = false"
            
            results = self.service.files().list(
                q=query,
                spaces='drive',
                fields='files(id, name, webViewLink)'
            ).execute()
            
            files = results.get('files', [])
            
            if files:
                file_obj = files[0]
                file_id = file_obj.get('id')
                file_url = file_obj.get('webViewLink')
                
                logger.info(f"Found file '{target_name}' | ID: {file_id} | URL: {file_url}")
                return file_id
            
            logger.warning(f"File named '{target_name}' not found in folder ID: {parent_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to get spreadsheet ID by name: {e}")
            return None

# สร้าง Object ตัวแทนสำหรับเรียกใช้งานแบบ Singleton
drive_service = GoogleDriveService()