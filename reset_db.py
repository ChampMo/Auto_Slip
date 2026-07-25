from database.session import engine, Base
# นำเข้า models ทุกตัวเพื่อให้ SQLAlchemy รู้จักโครงสร้างตารางทั้งหมดที่จะสร้างกลับมา
from database.models import Transaction, UsedQR, AuditLog

def reset_database():
    print("🔄 กำลังลบตารางเก่าทั้งหมด (Drop Tables)...")
    # สั่งลบตารางทั้งหมดทิ้ง
    Base.metadata.drop_all(bind=engine)
    
    print("🏗️ กำลังสร้างตารางใหม่ทั้งหมด (Create Tables)...")
    # สั่งสร้างตารางใหม่ตามโครงสร้างใน models.py ปัจจุบัน
    Base.metadata.create_all(bind=engine)
    
    print("✨ ล้างและรีเซ็ตฐานข้อมูลสำเร็จเรียบร้อยแล้ว!")

if __name__ == "__main__":
    confirm = input("⚠️ คุณต้องการล้างข้อมูลทั้งหมดใน Database จริงๆใช่ไหม? (y/n): ")
    if confirm.lower() == 'y':
        reset_database()
    else:
        print("❌ ยกเลิกการทำงาน")