import re

# ป้ายที่เจอจริงในกลุ่ม เรียงจากยาวไปสั้นเสมอ
# ("AMOUNT THB" ต้องมาก่อน "AMOUNT" และ "FULL NAME" ต้องมาก่อน "NAME")
TRANS_ID_LABELS = [
    "TRANSACTION ID", "TRANS ID", "TRANSID", "TRX. ID", "TRX ID", "TRXID", "REF ID", "REF NO",
]
USER_LABELS = [
    "USER NAME", "USERNAME", "USER ID", "USER", "MEMBER ID", "MEMBER",
]
FULLNAME_LABELS = [
    "FULL NAME", "FULLNAME", "SENDER NAME", "MEMBER NAME", "NAME", "ชื่อผู้โอน", "ชื่อ",
]
AMOUNT_LABELS = [
    "AMOUNT THB", "AMOUNT", "AMT", "จำนวนเงิน", "ยอดเงิน", "ยอด",
]

# หน่วยเงินที่อาจโผล่หน้าตัวเลข ("Amount : THB 500") — HB คือ THB ที่พิมพ์ตก
CURRENCY_BEFORE = r"(?:THB|THAI\s*BAHT|BAHT|HB|บาท|฿)"

# ตัวเลขเดี่ยว รองรับคอมมาคั่นหลักพันและทศนิยม
NUMBER = r"[\d,]+(?:\.\d+)?"


def _synonym_pattern(label: str) -> str:
    """'TRANS ID' -> 'TRANS\\s*ID' เพื่อให้จับได้ทั้ง 'TRANS ID', 'TRANSID', 'TRANS  ID'"""
    return r"\s*".join(re.escape(part) for part in label.split())


def _labels_pattern(labels: list) -> str:
    return "|".join(_synonym_pattern(label) for label in labels)


def _labels_pattern_group(labels: list) -> str:
    # เรียงจากยาวไปสั้น เพื่อให้ "FULL NAME" ชนะ "NAME" เมื่ออยู่ตำแหน่งเดียวกัน
    return _labels_pattern(sorted(labels, key=len, reverse=True))


# ป้ายส่วนใหญ่ปลอดภัยพอที่จะหาได้ทุกตำแหน่ง ขอแค่มีตัวคั่นอยู่ข้างหน้า
# (กัน "xAMOUNT" แต่ยอมให้ "FULL NAME : ก AMOUNT : 100" ที่พิมพ์รวมมาบรรทัดเดียว)
ANCHOR_ANY = r"(?:^|(?<=[\s,;|/•·\-]))"

# ป้ายคำว่า "NAME" เดี่ยวๆ อันตราย เพราะไปโผล่ใน "Bank Name" กับ "Username" ได้
# จึงบังคับให้อยู่ต้นบรรทัดหรือหลังคอมมาเท่านั้น (ยอมมีช่องว่าง/ขีดนำหน้าได้)
ANCHOR_LINE_START = r"(?:^[\s\-•*·]*|,\s*)"

# ป้ายชื่อที่เขียนเต็ม ไม่กำกวม จึงหาได้ทุกตำแหน่ง
EXPLICIT_NAME_LABELS = ["FULL NAME", "FULLNAME", "SENDER NAME", "MEMBER NAME", "ชื่อผู้โอน"]
BARE_NAME_LABELS = ["NAME", "ชื่อ"]

TRANS_ID_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(TRANS_ID_LABELS)})\s*[:=]\s*(\S+)")
USER_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(USER_LABELS)})\s*[:=]\s*(\S+)")
FULLNAME_PATTERN = re.compile(
    rf"(?im)(?:{ANCHOR_ANY}(?:{_labels_pattern(EXPLICIT_NAME_LABELS)})"
    rf"|{ANCHOR_LINE_START}(?:{_labels_pattern(BARE_NAME_LABELS)}))\s*[:=]\s*([^\n]+)")

# ยอดเงินเป็นกรณีพิเศษ: บางข้อความไม่มี : เลย ("Amount 400 HB")
# และรองรับการบวก ("100+100 = 200") โดยเก็บทั้งตัวตั้งและผลลัพธ์ที่เขาเขียน
AMOUNT_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(AMOUNT_LABELS)})\s*[:=]?\s*{CURRENCY_BEFORE}?\s*"
    rf"({NUMBER}(?:\s*\+\s*{NUMBER})*)"
    rf"(?:\s*=\s*({NUMBER}))?"
)

# ใช้ตัดหางชื่อ ตอนที่เขาพิมพ์หลายป้ายรวมมาในบรรทัดเดียว
NEXT_LABEL_PATTERN = re.compile(
    r"(?i)\s*(?:"
    + _labels_pattern_group(TRANS_ID_LABELS + USER_LABELS + FULLNAME_LABELS + AMOUNT_LABELS)
    + r")\s*[:=]"
)


def _cut_at_next_label(value: str) -> str:
    """'มณฑล สุขจินดา AMOUNT THB : 200.00' -> 'มณฑล สุขจินดา'"""
    match = NEXT_LABEL_PATTERN.search(value)
    return value[:match.start()] if match else value


def parse_number(text: str):
    """แปลงข้อความเป็นตัวเลข คืน None ถ้าแปลงไม่ได้"""
    try:
        return float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def parse_typed_amount(text: str):
    """อ่านยอดที่แอดมินพิมพ์ตอบกลับมาเอง คืน None ถ้าไม่ใช่ยอดที่ใช้ได้

    ยอมให้พิมพ์หน่วยติดมาด้วย ("1,500.00 THB") แต่ยอดต้องมากกว่าศูนย์
    """
    match = re.search(NUMBER, str(text or ""))
    if not match:
        return None
    value = parse_number(match.group(0))
    if value is None or value <= 0:
        return None
    return value


def parse_amount_expression(expression: str, declared: str = None) -> tuple:
    """อ่านนิพจน์ยอดเงิน คืน (ยอด, คำเตือนถ้าบวกไม่ลง)

    '1,100.00'       -> (1100.0, None)
    '100+100'        -> (200.0, None)
    '100+100' = 200  -> (200.0, None)
    '100+100' = 300  -> (300.0, "...")   ยึดตามที่เขาเขียน แต่ติดธงไว้ว่าไม่ตรง
    """
    addends = [parse_number(part) for part in re.split(r"\+", expression or "")]
    addends = [value for value in addends if value is not None]
    if not addends:
        return None, None

    calculated = round(sum(addends), 2)
    stated = parse_number(declared) if declared is not None else None

    if stated is None:
        return calculated, None

    if len(addends) > 1 and round(stated, 2) != calculated:
        warning = (
            f"The caption does not add up: "
            f"{' + '.join(f'{value:g}' for value in addends)} = {calculated:g}, but it says {stated:g}."
        )
        return stated, warning

    return stated, None


def extract_data_from_caption(caption: str) -> dict:
    """ดึงข้อมูลจาก caption โดยอ่านจาก 'ป้าย' เท่านั้น ไม่กวาดตัวเลขทั้งข้อความ

    การกวาดตัวเลขทั้งข้อความอันตราย เพราะบาง format มีทั้งวันที่และเลขบัญชีปนอยู่
    เช่น 'Trx. Date: 2026-08-02' และ 'Account No: 2273266332'
    """
    data = {
        "amount": None,
        "user_id": None,
        "trans_id": None,
        "fullname": None,
        "amount_note": None,
    }
    text = (caption or "").replace(" ", " ")
    if not text.strip():
        return data

    amount_match = AMOUNT_PATTERN.search(text)
    if amount_match:
        data["amount"], data["amount_note"] = parse_amount_expression(
            amount_match.group(1), amount_match.group(2)
        )

    user_match = USER_PATTERN.search(text)
    if user_match:
        data["user_id"] = user_match.group(1)

    trans_match = TRANS_ID_PATTERN.search(text)
    if trans_match:
        data["trans_id"] = trans_match.group(1)

    name_match = FULLNAME_PATTERN.search(text)
    if name_match:
        data["fullname"] = _cut_at_next_label(name_match.group(1)).strip() or None

    return data
