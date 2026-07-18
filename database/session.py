from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# 1. สร้าง Base ไว้ตรงนี้ เพื่อให้ models.py มาเรียกใช้ได้
Base = declarative_base()

# 2. ตั้งค่าการเชื่อมต่อ SQLite
SQLALCHEMY_DATABASE_URL = "sqlite:///./slips_data.db"
engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    # 3. 🛑 แก้ปัญหา Circular Import: ย้ายการเรียก models มาไว้ "ข้างใน" ฟังก์ชันนี้แทน
    import database.models
    
    # 4. สั่งสร้างตาราง
    Base.metadata.create_all(bind=engine)
    print("✅ สร้างตารางและเชื่อมต่อ Database สำเร็จ!")