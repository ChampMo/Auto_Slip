import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# 1. สร้าง Base ไว้ตรงนี้ เพื่อให้ models.py มาเรียกใช้ได้
Base = declarative_base()

# 2. ดึงค่าจาก .env (ถ้าไม่มีจะใช้ sqlite สำรองไว้ตอนรันทดสอบในเครื่อง)
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")

# 3. สร้าง Engine (แยกเงื่อนไขเพราะ SQLite ต้องการ connect_args เพิ่มเติม)
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    # 🛑 แก้ปัญหา Circular Import: ย้ายการเรียก models มาไว้ "ข้างใน" ฟังก์ชันนี้แทน
    import database.models
    
    # สั่งสร้างตาราง
    Base.metadata.create_all(bind=engine)
    print(f"✅ สร้างตารางและเชื่อมต่อ Database สำเร็จ! ({SQLALCHEMY_DATABASE_URL})")