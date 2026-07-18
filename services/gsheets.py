import gspread
from google.oauth2.service_account import Credentials
from core.config import config
from datetime import datetime

# ตั้งค่าสิทธิ์การเข้าถึง
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_sheet():
    try:
        # โหลดไฟล์กุญแจ credentials.json ที่ได้จาก Google Cloud
        creds = Credentials.from_service_account_file("credentials.json", scopes=SCOPES)
        client = gspread.authorize(creds)
        
        # เปิดไฟล์ Sheet ตาม ID และเลือก Sheet แผ่นแรก (sheet1)
        sheet = client.open_by_key(config.SPREADSHEET_ID).sheet1
        return sheet
    except Exception as e:
        print(f"⚠️ Google Sheets Connection Error: {e}")
        return None

def append_to_sheet(txn):
    sheet = get_sheet()
    if not sheet:
        return False
        
    try:
        # จัดเตรียมข้อมูลเรียงตามคอลัมน์ (ปรับแก้ลำดับได้ตามหน้าตา Sheet ของคุณ)
        now = datetime.now()
        date_str = now.strftime("%d/%m/%Y")
        time_str = now.strftime("%H:%M:%S")
        
        row = [
            date_str,                           
            time_str,                           
            txn.chat_user_id or "-",            
            txn.chat_trans_id or "-",           
            txn.chat_fullname or "-",           
            txn.sender_names or "-",             # ดึงชื่อคนโอนทุกคน
            txn.api_total_amount or txn.chat_amount, # ดึงยอดรวม API
            txn.status,                         
            txn.batch_id                         # ใช้ ID กลุ่มแทน
            ]
        
        # สั่งเพิ่มข้อมูลต่อท้ายบรรทัดล่างสุด
        sheet.append_row(row)
        return True
    except Exception as e:
        print(f"⚠️ Append to Sheet Error: {e}")
        return False