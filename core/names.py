import re


# คำนำหน้าภาษาไทย เรียงจากยาวไปสั้น เพื่อไม่ให้ "นาง" ไปตัดหน้า "นางสาว"
# ภาษาไทยมักเขียนติดกับชื่อ (เช่น "นายสมชาย") จึงตัดโดยไม่ต้องมีตัวคั่น
NAME_TITLES = ["นางสาว", "นาย", "น.ส.", "น.ส", "นาง"]

# คำนำหน้าภาษาอังกฤษ ต้องตามด้วยช่องว่าง (มีจุดหรือไม่มีก็ได้)
# ถ้าไม่บังคับให้มีตัวคั่น "MR" จะไปตัดหน้า "MRS" และชื่อที่ขึ้นต้นด้วย MS/DR จะโดนตัดผิด
# เรียง MRS/MISS ไว้ก่อน MR/MS เพราะ regex เลือกตัวที่ match ตัวแรก
ENGLISH_NAME_TITLE_PATTERN = re.compile(r"(?i)^(MRS|MISS|MR|MS|DR)\.?\s+")

# ค่าที่ API ส่งกลับมาเมื่ออ่านชื่อผู้ส่งไม่ได้ ให้ถือว่าไม่มีชื่อ
UNKNOWN_NAMES = {"unknown", "ไม่ระบุชื่อ", "-", ""}


def strip_name_title(value: str) -> str:
    """ตัดคำนำหน้าชื่อออก รองรับทั้งไทยและอังกฤษ

    'นาย ดุลยฤทธิ์ ส' -> 'ดุลยฤทธิ์ ส'
    'MR ATHIWAT N'    -> 'ATHIWAT N'
    """
    cleaned = (value or "").strip()

    for prefix in NAME_TITLES:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):].strip()

    english_title = ENGLISH_NAME_TITLE_PATTERN.match(cleaned)
    if english_title:
        return cleaned[english_title.end():].strip()

    return cleaned


def get_first_name(value: str) -> str:
    """ดึงเฉพาะชื่อจริง เช่น 'นาย ดุลยฤทธิ์ ส' -> 'ดุลยฤทธิ์'"""
    if (value or "").strip().lower() in UNKNOWN_NAMES:
        return ""

    parts = [part for part in re.split(r"\s+", strip_name_title(value)) if part]
    return parts[0] if parts else ""


def split_bank_name(value: str) -> tuple[str, str]:
    """แยกชื่อเป็น (ชื่อจริง, อักษรย่อนามสกุล) แบบที่ธนาคารแสดงบนสลิป

    เช่น 'นาย ดุลยฤทธิ์ ส' -> ('ดุลยฤทธิ์', 'ส') และ 'ดุลยฤทธิ์' -> ('ดุลยฤทธิ์', '')
    """
    parts = [part for part in re.split(r"\s+", strip_name_title(value)) if part]
    if not parts:
        return "", ""

    first_name = re.sub(r"[^\wก-๙]", "", parts[0]).lower()
    last_name_initial = ""
    if len(parts) > 1:
        last_name_initial = re.sub(r"[^\wก-๙]", "", parts[-1])[:1].lower()
    return first_name, last_name_initial


def get_first_names(value: str) -> str:
    """ดึงชื่อจริงจากรายชื่อที่คั่นด้วย , (กรณีรูปเดียวมีหลายสลิป)"""
    names = [get_first_name(name) for name in (value or "").split(",")]
    return ", ".join(name for name in names if name)
