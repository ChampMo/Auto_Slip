import logging
from datetime import datetime
from services.gsheets import append_to_sheet

# ตั้งค่า Logging เพื่อให้เห็นขั้นตอนการสร้างโฟลเดอร์/ชีตใน Terminal ชัดเจน
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# 1. จำลองคลาส Transaction (Mock Object) ให้มีโครงสร้างตามที่ handlers.py เรียกใช้
class MockTransaction:
    def __init__(self):
        self.chat_user_id = "123456789"
        self.chat_trans_id = "MOCK-TXN-778899"
        self.chat_fullname = "Somchai Dev"
        self.sender_names = "นาย สมชาย โอนไว"
        self.chat_amount = 500.00
        self.api_total_amount = 500.00
        self.status = "Receive"  # ลองเปลี่ยนเป็น 'Reject' เพื่อเทสได้เช่นกัน
        self.batch_id = "batch_mock_20260720_xyz"

def run_test():
    print("🚀 [TEST] เริ่มต้นทดสอบระบบ Google Drive & Sheets อัตโนมัติ...")
    print("⏳ กำลังเตรียมส่งข้อมูลจำลองไปยังระบบ...")
    
    # 2. สร้างข้อมูลธุรกรรมจำลอง
    mock_txn = MockTransaction()
    
    # 3. เรียกใช้งานฟังก์ชันบันทึกข้อมูล
    # ฟังก์ชันนี้จะวิ่งไปเช็ก ปี -> เดือน -> วัน บน Google Drive อัตโนมัติ
    success = append_to_sheet(mock_txn)
    
    print("--------------------------------------------------")
    if success:
        print("✅ [TEST SUCCESS] บันทึกข้อมูลสำเร็จ!")
        print("📌 โปรดตรวจสอบผลลัพธ์บน Google Drive ของคุณ:")
        print(f"   1. โฟลเดอร์ปีปัจจุบัน: '{datetime.now().strftime('%Y')}'")
        print(f"   2. ไฟล์ Google Sheets เดือนปัจจุบัน: '{datetime.now().strftime('%Y-%m')}'")
        print(f"   3. แท็บวันปัจจุบัน: '{datetime.now().strftime('%d')}'")
    else:
        print("❌ [TEST FAILED] การบันทึกล้มเหลว กรุณาเช็ก Error log ด้านบน")
    print("--------------------------------------------------")

if __name__ == "__main__":
    run_test()