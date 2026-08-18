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


def safe_database_url() -> str:
    """ที่อยู่ฐานข้อมูลแบบซ่อนรหัสผ่าน ไว้พิมพ์ลง log

    ห้ามพิมพ์ URL ดิบเด็ดขาด เพราะมีรหัสผ่านอยู่ในนั้น
    log ถูกส่งต่อให้คนอื่นช่วยดูได้ตลอด รหัสจะหลุดไปโดยไม่มีใครสังเกต
    """
    url = str(SQLALCHEMY_DATABASE_URL)
    if "://" not in url:
        return url

    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url

    credentials, host = rest.rsplit("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"

# คอลัมน์ที่เพิ่มเข้ามาทีหลัง (ตารางเก่าที่สร้างไว้แล้วจะไม่ถูก create_all อัปเดตให้)
_ADDED_COLUMNS = {
    "transactions": {
        "chat_id": "VARCHAR(50)",
        "msg_id": "VARCHAR(50)",
        "raw_caption": "VARCHAR(500)",
        "receiver_names": "VARCHAR(300)",
        "receiver_account": "VARCHAR(255)",
        # เหตุผลของด่านบัญชีผู้รับ เพิ่มใน v1.16.0 — แถวเก่าจะเป็น NULL
        "receiver_note": "VARCHAR(255)",
        "caption_warning": "VARCHAR(200)",
        "review_msg_id": "VARCHAR(50)",
        # เวลาโอนจากสลิป เพิ่มเข้ามาใน v1.2.0 — แถวเก่าจะเป็น NULL
        "transfer_at": "TIMESTAMP",
        "transfer_time_text": "VARCHAR(120)",
        # กันสลิปซ้ำสำหรับใบที่อ่าน QR ไม่ออก เพิ่มใน v1.3.0
        "photo_hash": "VARCHAR(64)",
        "qr_request_msg_id": "VARCHAR(50)",
    },
    "audit_logs": {
        "actor": "VARCHAR(100)",
    },
}


def _widen_columns():
    """ขยายคอลัมน์ที่เคยตั้งไว้สั้นเกินจริง (เฉพาะ Postgres)

    create_all สร้างเฉพาะตารางที่ยังไม่มี ไม่แก้ความยาวคอลัมน์ของตารางเดิม
    ฐานข้อมูลที่ใช้งานอยู่แล้วจึงยังเป็นความยาวเก่า และ INSERT จะล้มเมื่อค่ายาวเกิน
    SQLite ไม่บังคับความยาวอยู่แล้ว จึงข้ามไป
    """
    if not str(SQLALCHEMY_DATABASE_URL).startswith("postgres"):
        return

    import database.models as models

    targets = {
        "transactions": {
            "raw_caption": models.CAPTION_MAX,
            "chat_fullname": models.NAME_MAX,
            "caption_warning": models.WARNING_MAX,
            "sender_names": models.NAMES_LIST_MAX,
            "receiver_names": models.NAMES_LIST_MAX,
        },
        "used_qrs": {"qr_ref": models.QR_REF_MAX},
        "audit_logs": {"action": models.ACTION_MAX},
    }

    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())

            for table_name, columns in targets.items():
                if table_name not in table_names:
                    continue
                current = {col["name"]: col for col in inspector.get_columns(table_name)}
                for column_name, wanted in columns.items():
                    col = current.get(column_name)
                    if col is None:
                        continue
                    length = getattr(col["type"], "length", None)
                    if length is not None and length < wanted:
                        conn.execute(text(
                            f"ALTER TABLE {table_name} "
                            f"ALTER COLUMN {column_name} TYPE VARCHAR({wanted})"
                        ))
                        print(f"✅ ขยายคอลัมน์ {table_name}.{column_name}: {length} → {wanted}")
    except Exception as exc:
        print(f"⚠️ ขยายความยาวคอลัมน์ไม่สำเร็จ: {exc}")


def _ensure_columns():
    """Add missing columns to existing tables without breaking older schemas."""
    try:
        with engine.begin() as conn:
            inspector = inspect(conn)
            table_names = set(inspector.get_table_names())

            for table_name, columns in _ADDED_COLUMNS.items():
                if table_name not in table_names:
                    continue

                existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
                for column_name, column_type in columns.items():
                    if column_name not in existing_columns:
                        conn.execute(
                            text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
                        )
                        print(f"✅ Added missing column: {table_name}.{column_name}")
    except Exception as exc:
        print(f"⚠️ Could not ensure database columns: {exc}")


def init_db():
    # 🛑 แก้ปัญหา Circular Import: ย้ายการเรียก models มาไว้ "ข้างใน" ฟังก์ชันนี้แทน
    import database.models

    # สั่งสร้างตาราง
    Base.metadata.create_all(bind=engine)
    _ensure_columns()
    _widen_columns()
    print(f"✅ Database initialized successfully! ({safe_database_url()})")