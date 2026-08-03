import re


# เรียงคำนำหน้าจากยาวไปสั้น เพื่อไม่ให้ "นาง" ไปตัดหน้า "นางสาว"
NAME_TITLES = ["นางสาว", "นาย", "น.ส.", "น.ส", "นาง"]

# ค่าที่ API ส่งกลับมาเมื่ออ่านชื่อผู้ส่งไม่ได้ ให้ถือว่าไม่มีชื่อ
UNKNOWN_NAMES = {"unknown", "ไม่ระบุชื่อ", "-", ""}


def strip_name_title(value: str) -> str:
    """ตัดคำนำหน้าชื่อออก เช่น 'นาย ดุลยฤทธิ์ ส' -> 'ดุลยฤทธิ์ ส'"""
    cleaned = (value or "").strip()
    for prefix in NAME_TITLES:
        if cleaned.startswith(prefix):
            return cleaned[len(prefix):].strip()
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
