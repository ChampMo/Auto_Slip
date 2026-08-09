"""ทดสอบค่า Trans ID ที่จะถูกเขียนลงชีท"""
import sys

from core.names import (
    get_first_name,
    get_first_names,
    split_bank_name,
    split_name_variants,
    strip_name_title,
    surname_initials,
)


def sheet_trans_id(chat_trans_id, sender_names):
    """ลอจิกเดียวกับ services/gsheets.py append_to_sheet"""
    return chat_trans_id or get_first_names(sender_names) or ""


CASES = [
    ("มี Trans ID -> ใช้ Trans ID", ("0000004", "นาย ดุลยฤทธิ์ ส"), "0000004"),
    ("ไม่มี Trans ID -> ชื่อผู้ส่ง (เคสในรูป)", (None, "นาย ดุลยฤทธิ์ ส"), "ดุลยฤทธิ์"),
    ("คำนำหน้า นางสาว", (None, "นางสาว ปิยะดา ก"), "ปิยะดา"),
    ("คำนำหน้า น.ส.", (None, "น.ส. สมหญิง จ"), "สมหญิง"),
    ("คำนำหน้า นาง", (None, "นาง สมศรี ท"), "สมศรี"),
    ("ไม่มีคำนำหน้า", (None, "ดุลยฤทธิ์ สมบูรณ์"), "ดุลยฤทธิ์"),
    ("ชื่ออังกฤษ", (None, "JOHN DOE"), "JOHN"),
    ("ไม่มีทั้งคู่ -> ว่าง", (None, None), ""),
    ("API อ่านชื่อไม่ได้ -> ว่าง", (None, "Unknown"), ""),
    ("API ไม่ระบุชื่อ -> ว่าง", (None, "ไม่ระบุชื่อ"), ""),
    ("Trans ID ว่างเปล่า -> ชื่อผู้ส่ง", ("", "นาย ดุลยฤทธิ์ ส"), "ดุลยฤทธิ์"),
    ("หลายสลิปในรูปเดียว", (None, "นาย ดุลยฤทธิ์ ส, นางสาว ปิยะดา ก"), "ดุลยฤทธิ์, ปิยะดา"),
    ("หลายสลิป อ่านชื่อได้บางใบ", (None, "นาย ดุลยฤทธิ์ ส, Unknown"), "ดุลยฤทธิ์"),
]

failed = 0
for label, (trans_id, senders), expected in CASES:
    got = sheet_trans_id(trans_id, senders)
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")

# normalize_bank_name ต้องให้ผลเหมือนเดิมหลังย้ายไปใช้ strip_name_title
sys.path.insert(0, ".")
old_prefixes = ["นาย", "นางสาว", "น.ส.", "น.ส", "นาง"]


def normalize_old(value):
    import re
    cleaned = (value or "").strip()
    for prefix in old_prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):].strip()
            break
    parts = [p for p in re.split(r"\s+", cleaned) if p]
    if not parts:
        return ""
    first_name = re.sub(r"[^\wก-๙]", "", parts[0])
    last_initial = re.sub(r"[^\wก-๙]", "", parts[-1])[:1] if len(parts) > 1 else ""
    return re.sub(r"[\s\._,-]+", "", f"{first_name}{last_initial}").lower()


def normalize_bank_name(value):
    """สำเนาลอจิกใหม่ใน bot/handlers.py (เลี่ยง import telegram ที่ยังไม่ได้ติดตั้ง)"""
    import re
    parts = [p for p in re.split(r"\s+", strip_name_title(value)) if p]
    if not parts:
        return ""
    first_name = re.sub(r"[^\wก-๙]", "", parts[0])
    last_initial = re.sub(r"[^\wก-๙]", "", parts[-1])[:1] if len(parts) > 1 else ""
    return re.sub(r"[\s\._,-]+", "", f"{first_name}{last_initial}").lower()


for sample in ["นาย ดุลยฤทธิ์ ส", "นางสาว ปิยะดา ก", "น.ส.สมหญิง จ", "นาง สมศรี ท",
               "ดุลยฤทธิ์ สมบูรณ์", "JOHN DOE", "", "   ", "นายก"]:
    got, expected = normalize_bank_name(sample), normalize_old(sample)
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] normalize_bank_name({sample!r}): got={got!r} old={expected!r}")

print("\n=== แชทเขียนชื่อสองภาษาคั่นด้วย / ===")
# เคสจริง: สลิปโชว์ "นาย ธีระชัย ค." แต่แชทเขียน "ธีระชัย คำสี / THEERACHAI KHAMSEE"
# ถ้ามองเป็นชื่อเดียว คำสุดท้าย KHAMSEE จะกลายเป็นนามสกุล แล้วเทียบกับ ค. ไม่ตรง
for label, value, expected in [
    ("แยกสองภาษา", "ธีระชัย คำสี / THEERACHAI KHAMSEE", ["ธีระชัย คำสี", "THEERACHAI KHAMSEE"]),
    ("ใช้ | คั่น", "ธีระชัย คำสี | THEERACHAI KHAMSEE", ["ธีระชัย คำสี", "THEERACHAI KHAMSEE"]),
    ("ชื่อเดียว", "ดุลยฤทธิ์ ส", ["ดุลยฤทธิ์ ส"]),
    ("ค่าว่าง", "", [""]),
    ("None", None, [""]),
]:
    got = split_name_variants(value)
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] แยกชื่อ {label}: got={got!r} expected={expected!r}")

# สลิปจะโชว์แบบไหนก็ต้องเทียบติด จึงต้องได้คู่ที่ถูกต้องทั้งสองแบบ
pairs = [split_bank_name(v) for v in split_name_variants("ธีระชัย คำสี / THEERACHAI KHAMSEE")]
for label, expected in [("แบบไทย", ("ธีระชัย", "ค")), ("แบบอังกฤษ", ("theerachai", "k"))]:
    ok = expected in pairs
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}อยู่ในตัวเลือก: {expected!r} in {pairs!r}")

print("\n=== นามสกุลไทยหลายคำ (ณ ร้อยเอ็ด, ณ อยุธยา) ===")
# เคสจริง: สลิปโชว์ "นาย อัฏฐระชัย ณ" แชทเขียน "อัฏฐระชัย ณ ร้อยเอ็ด"
# นามสกุลคือ "ณ ร้อยเอ็ด" ธนาคารย่อเหลือ "ณ" แต่ถ้าดูแค่คำสุดท้ายจะได้ "ร" แล้วไม่ตรง
def bank_names_match(api, chat):
    """ลอจิกเดียวกับ bot/handlers.names_match_in_bank_format

    ที่ไม่ import ตรงๆ เพราะ handlers ลาก pyzbar มาด้วย ซึ่งไม่มีในเครื่องที่รันเทสต์
    """
    api_first, _ = split_bank_name(api)
    chat_first, _ = split_bank_name(chat)
    if not api_first or not chat_first or api_first != chat_first:
        return False
    api_initials, chat_initials = surname_initials(api), surname_initials(chat)
    if not api_initials or not chat_initials:
        return True
    return bool(api_initials & chat_initials)


for label, api, chat, expected in [
    ("เคสจริง ณ ร้อยเอ็ด", "นาย อัฏฐระชัย ณ", "อัฏฐระชัย ณ ร้อยเอ็ด", True),
    ("ณ อยุธยา", "นาย วรชัย ณ", "วรชัย ณ อยุธยา", True),
    ("ย่อคำท้ายของนามสกุลหลายคำ", "นาย วรชัย อ", "วรชัย ณ อยุธยา", True),
    ("นามสกุลคำเดียว ยังผ่านเหมือนเดิม", "นาย ดุลยฤทธิ์ ส", "ดุลยฤทธิ์ สมบูรณ์", True),
    ("สลิปไม่ให้นามสกุล", "นาย สมชาย", "สมชาย ทองดี", True),
    ("นามสกุลคนละตัว ต้องไม่ผ่าน", "นาย สมชาย ใ", "สมชาย ทองดี", False),
    ("ชื่อจริงคนละคน ต้องไม่ผ่าน", "นาย สมหญิง ท", "สมชาย ทองดี", False),
    ("ณ ที่ไม่ได้อยู่ในนามสกุลจริง ต้องไม่ผ่าน", "นาย สมชาย ณ", "สมชาย ทองดี", False),
]:
    got = bank_names_match(api, chat)
    ok = got == expected
    failed += 0 if ok else 1
    print(f"[{'PASS' if ok else 'FAIL'}] {label}: got={got} expected={expected}")

print("\nRESULT:", "ALL PASS" if failed == 0 else f"{failed} FAILED")
