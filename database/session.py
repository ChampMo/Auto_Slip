import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# 1. สร้าง Base ไว้ตรงนี้ เพื่อให้ models.py มาเรียกใช้ได้
Base = declarative_base()

# 2. ดึงค่าจาก .env (ถ้าไม่มีจะใช้ sqlite สำรองไว้ตอนรันทดสอบในเครื่อง)
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./slip.db")

# 3. สร้าง Engine (แยกเงื่อนไขเพราะ SQLite ต้องการ connect_args เพิ่มเติม)
if str(SQLALCHEMY_DATABASE_URL).startswith("sqlite"):
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
    )
else:
    engine = create_engine(SQLALCHEMY_DATABASE_URL)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# คอลัมน์ที่เพิ่มเข้ามาทีหลัง (ตารางเก่าที่สร้างไว้แล้วจะไม่ถูก create_all อัปเดตให้)
_TRANSACTION_COLUMNS = {
    "chat_id": "VARCHAR(50)",
    "msg_id": "VARCHAR(50)",
    "raw_caption": "VARCHAR(500)",
    "receiver_names": "VARCHAR(300)",
    "receiver_account": "VARCHAR(255)",
}


def _ensure_columns():
    """Add missing columns to existing tables without breaking older schemas."""
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            if "transactions" not in inspector.get_table_names():
                return

            existing_columns = {col["name"] for col in inspector.get_columns("transactions")}
            for column_name, column_type in _TRANSACTION_COLUMNS.items():
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE transactions ADD COLUMN {column_name} {column_type}"))
                    print(f"✅ Added missing column: transactions.{column_name}")
    except Exception as exc:
        print(f"⚠️ Could not ensure database columns: {exc}")


def init_db():
    # 🛑 แก้ปัญหา Circular Import: ย้ายการเรียก models มาไว้ "ข้างใน" ฟังก์ชันนี้แทน
    import database.models

    # สั่งสร้างตาราง
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    print(f"✅ Database initialized successfully! ({SQLALCHEMY_DATABASE_URL})")