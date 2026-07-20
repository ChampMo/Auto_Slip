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
            # โหลด Credentials จาก Path ที่ตั้งไว้ใน Config
            creds = service_account.Credentials.from_service_account_file(
                config.GOOGLE_CREDS_PATH, 
                scopes=scopes
            )
            self.service = build('drive', 'v3', credentials=creds)
            logger.info("Google Drive Service initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive Service: {e}")
            raise e

    def get_or_create_year_folder(self, year: str) -> str:
        """
        ค้นหาโฟลเดอร์ปีใน MAIN_FOLDER_ID ถ้าไม่มีให้สร้างใหม่
        :param year: สตริงระบุปี เช่น '2026'
        :return: Folder ID ของปีนั้นๆ
        """
        parent_id = config.MAIN_FOLDER_ID
        if not parent_id:
            raise ValueError("MAIN_FOLDER_ID is not set in the configuration/environment.")

        # ค้นหาโฟลเดอร์ที่มีชื่อตรงกับปี และต้องไม่อยู่ในถังขยะ
        query = (
            f"name = '{year}' and "
            f"'{parent_id}' in parents and "
            f"mimeType = 'application/vnd.google-apps.folder' and "
            f"trashed = false"
        )
        
        results = self.service.files().list(
            q=query, 
            spaces='drive', 
            fields='files(id, name)'
        ).execute()
        files = results.get('files', [])

        if files:
            logger.info(f"Found existing year folder '{year}': {files[0]['id']}")
            return files[0]['id']

        # ถ้าไม่พบ ให้สร้างโฟลเดอร์ใหม่
        file_metadata = {
            'name': year,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        
        folder = self.service.files().create(
            body=file_metadata, 
            fields='id'
        ).execute()
        
        logger.info(f"Created new year folder '{year}': {folder['id']}")
        return folder['id']

    def get_or_create_month_sheet(self, parent_folder_id, month_name):
        """ค้นหาไฟล์ประจำเดือน ถ้าไม่เจอ ให้สร้างไฟล์ Google Sheets ขึ้นมาใหม่ในโฟลเดอร์ปีทันที"""
        try:
            # 1. ค้นหาไฟล์เดิมก่อน
            query = f"name = '{month_name}' and '{parent_folder_id}' in parents and mimeType = 'application/vnd.google-apps.spreadsheet' and trashed = false"
            results = self.service.files().list(q=query, fields="files(id, name)").execute()
            files = results.get('files', [])

            if files:
                logger.info(f"Found existing month sheet '{month_name}': {files[0]['id']}")
                return files[0]['id']

            # 🌟 2. ถ้าไม่เจอ (เกิด Event Month sheet not found) ให้สร้างขึ้นมาด้วย Drive API ตรงนี้เลย
            logger.info(f"Month sheet '{month_name}' not found. Creating via Drive API...")
            file_metadata = {
                'name': month_name,
                'mimeType': 'application/vnd.google-apps.spreadsheet', # กำหนดว่าเป็น Google Sheets
                'parents': [parent_folder_id] # บังคับให้อยู่ในโฟลเดอร์ปีทันที
            }
            
            # สั่งสร้างไฟล์
            new_file = self.service.files().create(body=file_metadata, fields='id').execute()
            new_sheet_id = new_file.get('id')
            
            logger.info(f"Successfully created month sheet via Drive API. ID: {new_sheet_id}")
            return new_sheet_id

        except Exception as e:
            logger.error(f"Error in get_or_create_month_sheet: {e}")
            return None

# สร้าง Object ตัวแทนสำหรับเรียกใช้งานแบบ Singleton
drive_service = GoogleDriveService()