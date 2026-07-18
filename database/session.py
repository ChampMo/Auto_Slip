from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.models import Base
from core.config import config

# สร้าง Engine สำหรับเชื่อมต่อ Database
# (check_same_thread=False จำเป็นสำหรับ SQLite เมื่อบอททำงานแบบหลายคำสั่งพร้อมกัน)
engine = create_engine(
    config.DATABASE_URL, 
    connect_args={"check_same_thread": False} if "sqlite" in config.DATABASE_URL else {}
)

# สร้าง Session Factory สำหรับเปิดการเชื่อมต่อ (Transaction) ไปยัง DB
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """ฟังก์ชันสำหรับสร้างตารางทั้งหมดที่ออกแบบไว้ใน models.py"""
    Base.metadata.create_all(bind=engine)
    print("✅ สร้างตารางและเชื่อมต่อ Database สำเร็จ!")

def get_db():
    """Generator สำหรับเรียกใช้ Session และปิดการเชื่อมต่ออัตโนมัติเพื่อป้องกัน DB ล็อก"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()