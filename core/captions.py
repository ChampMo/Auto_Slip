import re
from datetime import date, datetime

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

# ─────────────────────────────────────────────────────────────────────────────
# ช่องว่างแนวนอนเท่านั้น ห้ามข้ามบรรทัด
#
# ⚠️ จุดนี้เคยเป็นบั๊กร้ายแรง: เดิมใช้ \s ซึ่งรวม \n ด้วย
# พอเจอป้ายที่เว้นค่าว่างไว้ เช่น
#     Trans ID:
#     Name:   วรวุฒิ เงาโงน
# ระบบจะข้ามบรรทัดไปหยิบ "Name:" มาเป็นรหัสรายการ
# กรณียอดเงินอันตรายกว่า เพราะไปหยิบเลขบัญชีจากบรรทัดถัดไปมาเป็นจำนวนเงินได้
#
# ค่าของป้ายต้องอยู่บรรทัดเดียวกับป้ายเสมอ ป้ายที่เว้นว่าง = ไม่ได้แจ้งค่านั้น
# ─────────────────────────────────────────────────────────────────────────────
H = r"[^\S\r\n]*"

TRANS_ID_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(TRANS_ID_LABELS)}){H}[:=]{H}(\S+)")
USER_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(USER_LABELS)}){H}[:=]{H}(\S+)")
FULLNAME_PATTERN = re.compile(
    rf"(?im)(?:{ANCHOR_ANY}(?:{_labels_pattern(EXPLICIT_NAME_LABELS)})"
    rf"|{ANCHOR_LINE_START}(?:{_labels_pattern(BARE_NAME_LABELS)})){H}[:=]{H}([^\n]+)")

# ระหว่างป้ายกับตัวเลข มักมีหน่วยเงินและเครื่องหมายคั่นปนมาได้หลายแบบ
# เช่น "Amount : THB .- 900" หรือ "Amount: ฿1,500" — ข้ามให้หมดก่อนอ่านตัวเลข
# แต่ละรอบต้องกินอักขระอย่างน้อย 1 ตัว จึงวนไม่รู้จบไม่ได้
AMOUNT_NOISE = rf"(?:{H}(?:{CURRENCY_BEFORE}|[.\-–—:]+))*"

# ยอดเงินเป็นกรณีพิเศษ: บางข้อความไม่มี : เลย ("Amount 400 HB")
# และรองรับการบวก ("100+100 = 200") โดยเก็บทั้งตัวตั้งและผลลัพธ์ที่เขาเขียน
AMOUNT_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(AMOUNT_LABELS)}){H}[:=]?{AMOUNT_NOISE}{H}"
    rf"({NUMBER}(?:{H}\+{H}{NUMBER})*)"
    rf"(?:{H}={H}({NUMBER}))?"
)

# ใช้ตรวจว่า "มีป้ายยอดเงินอยู่ไหม" แยกจากการอ่านตัวเลขได้สำเร็จหรือไม่
# สองอย่างนี้ต่างกันมาก: ไม่มีป้าย = เขาไม่ได้แจ้งยอด / มีป้ายแต่อ่านไม่ออก = ระบบอ่านพลาด
AMOUNT_LABEL_PATTERN = re.compile(
    rf"(?im){ANCHOR_ANY}(?:{_labels_pattern(AMOUNT_LABELS)}){H}[:=]?(?=\s|$)"
)


# ตัวเลขที่ยืนอยู่เดี่ยวๆ ไม่ติดกับตัวอักษร
# กัน "gta6969" กับ "aree12b" ซึ่งเป็นชื่อผู้ใช้ ไม่ใช่จำนวนเงิน
STANDALONE_NUMBER_PATTERN = re.compile(rf"(?<![\w.]){NUMBER}(?![\w])")


def find_standalone_numbers(text: str) -> list:
    """ตัวเลขทุกตัวในข้อความที่ไม่ได้ติดอยู่กับตัวอักษร

    ใช้เฉพาะกรณีที่ต้องการ "เดาว่าอาจเป็นยอด" แล้วส่งให้คนยืนยัน
    ห้ามเอาไปใช้ตัดสินอัตโนมัติเด็ดขาด เพราะตัวเลขในข้อความมีทั้งวันที่และเลขบัญชีปนอยู่
    """
    values = []
    for raw in STANDALONE_NUMBER_PATTERN.findall(text or ""):
        value = parse_number(raw)
        if value is not None and value > 0:
            values.append(value)
    return values


def find_amount_line(text: str) -> str:
    """บรรทัดที่มีป้ายยอดเงิน ไว้โชว์ให้แอดมินเห็นว่าอ่านตรงไหนไม่ออก"""
    for line in (text or "").splitlines():
        if AMOUNT_LABEL_PATTERN.search(line):
            return " ".join(line.split())
    return ""

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


# เวลาที่แอดมินพิมพ์เอง รับได้ทั้ง 0:18 / 00:18 / 0.18 และหลายค่าคั่นด้วย , หรือเว้นวรรค
_TYPED_TIME = re.compile(r"(\d{1,2})[:.](\d{2})")


def parse_typed_times(text: str):
    """อ่านเวลาโอนที่แอดมินพิมพ์ตอบกลับมา คืนข้อความพร้อมลงชีท หรือ None ถ้าใช้ไม่ได้

    '0:18'        -> '0:18'
    '00:18'       -> '0:18'          (ตัดศูนย์นำหน้าให้ตรงกับที่ระบบเขียนเอง)
    '0:18, 0:37'  -> '0:18, 0:37'    (รูปเดียวมีหลายสลิป)
    '25:00'       -> None            (ไม่มีเวลานี้จริง)

    จงใจไม่เรียงใหม่ ให้เรียงตามที่เขาพิมพ์ เพราะเขาเห็นสลิปอยู่ตรงหน้าและรู้ว่าใบไหนก่อน
    """
    raw = str(text or "")
    matches = _TYPED_TIME.findall(raw)
    if not matches:
        return None

    # มีตัวเลขอื่นปนอยู่นอกเหนือรูปแบบเวลา = เดาไม่ได้ว่าเขาหมายถึงอะไร ให้พิมพ์ใหม่
    leftover = _TYPED_TIME.sub("", raw)
    if re.search(r"\d", leftover):
        return None

    times = []
    for hour_text, minute_text in matches:
        hour, minute = int(hour_text), int(minute_text)
        if hour > 23 or minute > 59:
            return None
        times.append(f"{hour}:{minute:02d}")
    return ", ".join(times)



# ── วันที่บนสลิปไทย ─────────────────────────────────────────────────
# สลิปเขียนปีเป็น พ.ศ. และย่อเดือนเป็นภาษาไทย เช่น "8 ส.ค. 69"
# ต้องรับให้ครบทุกแบบที่คนพิมพ์จริง ไม่งั้นการกันสลิปซ้ำจะเทียบคนละวัน
THAI_MONTHS = {
    "ม.ค.": 1, "มกราคม": 1, "ก.พ.": 2, "กุมภาพันธ์": 2, "มี.ค.": 3, "มีนาคม": 3,
    "เม.ย.": 4, "เมษายน": 4, "พ.ค.": 5, "พฤษภาคม": 5, "มิ.ย.": 6, "มิถุนายน": 6,
    "ก.ค.": 7, "กรกฎาคม": 7, "ส.ค.": 8, "สิงหาคม": 8, "ก.ย.": 9, "กันยายน": 9,
    "ต.ค.": 10, "ตุลาคม": 10, "พ.ย.": 11, "พฤศจิกายน": 11, "ธ.ค.": 12, "ธันวาคม": 12,
}

_ISO_DATE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DMY_DATE = re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\b")
_THAI_DATE = re.compile(
    r"\b(\d{1,2})\s*(" + "|".join(re.escape(m) for m in sorted(THAI_MONTHS, key=len, reverse=True))
    + r")\s*(\d{2,4})\b"
)


def resolve_year(raw_year: int, today=None) -> int:
    """แปลงปีที่คนพิมพ์ให้เป็น ค.ศ. รองรับทั้ง พ.ศ. และปีย่อ 2 หลัก

    69 -> 2026 (พ.ศ. 2569)   26 -> 2026   2569 -> 2026   2026 -> 2026

    เลือกตัวที่ใกล้ปีปัจจุบันที่สุด แทนการตั้งกฎตายตัว เพราะสลิปที่กรอกย้อนหลัง
    จะไม่เกินปีสองปี การเดาแบบนี้จึงแม่นกว่าและไม่พังตอนข้ามศตวรรษ
    """
    now = (today or datetime.now()).year
    candidates = []
    if raw_year >= 2400:
        candidates.append(raw_year - 543)
    elif raw_year >= 1900:
        candidates.append(raw_year)
    else:
        candidates.append(2500 + raw_year - 543)   # ปีย่อแบบ พ.ศ.
        candidates.append(2000 + raw_year)         # ปีย่อแบบ ค.ศ.
    return min(candidates, key=lambda year: abs(year - now))


def parse_typed_date(text: str):
    """หาวันที่ในข้อความที่แอดมินพิมพ์ คืน (date, ข้อความที่เหลือ)

    คืน (None, ข้อความเดิม) ถ้าไม่เจอวันที่ — ปลายทางจะได้รู้ว่าเขาพิมพ์มาแค่เวลา
    """
    raw = str(text or "")

    for pattern, order in ((_ISO_DATE, "ymd"), (_THAI_DATE, "dMy"), (_DMY_DATE, "dmy")):
        match = pattern.search(raw)
        if not match:
            continue
        try:
            if order == "ymd":
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            elif order == "dMy":
                day, month, year = int(match.group(1)), THAI_MONTHS[match.group(2)], int(match.group(3))
            else:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
            parsed = date(resolve_year(year), month, day)
        except (ValueError, KeyError):
            return None, raw
        return parsed, raw[:match.start()] + " " + raw[match.end():]

    return None, raw


def parse_typed_date_and_times(text: str):
    """อ่านวันและเวลาที่แอดมินพิมพ์ตอบกลับมา คืน (date, ข้อความเวลา)

    '8/8/69 0:18'        -> (date(2026, 8, 8), '0:18')
    '8 ส.ค. 69 0:18, 0:37' -> (date(2026, 8, 8), '0:18, 0:37')
    '0:18'                 -> (None, '0:18')          พิมพ์มาแค่เวลา
    'abc'                  -> (None, None)            ใช้ไม่ได้

    ต้องแยกวันออกก่อนแล้วค่อยหาเวลา ไม่งั้น '08.08.69' จะถูกอ่านว่าเป็นเวลา 8:08
    """
    parsed_date, remainder = parse_typed_date(text)
    return parsed_date, parse_typed_times(remainder)


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
        # ยอดศูนย์หรือติดลบไม่ใช่ยอดฝากที่ใช้ได้ ถือว่ายังไม่ได้แจ้งยอด
        # (ตรงกับตอนแอดมินพิมพ์ยอดเอง ซึ่งไม่รับค่าที่ไม่เกินศูนย์เหมือนกัน)
        # ติดธงไว้ด้วย เพื่อให้ไปเข้าทางให้คนตัดสิน แทนที่จะถูกปฏิเสธอัตโนมัติ
        if data["amount"] is not None and data["amount"] <= 0:
            data["amount"] = None
            data["amount_note"] = (
                f'The caption reports an amount of zero or less: "{find_amount_line(text)}"'
            )
    else:
        # มีป้ายบอกยอดแต่อ่านตัวเลขไม่ออก = ระบบอ่านพลาด ไม่ใช่เขาไม่ได้แจ้ง
        # ห้ามปฏิเสธอัตโนมัติเด็ดขาด ต้องส่งให้คนดูพร้อมโชว์บรรทัดที่อ่านไม่ออก
        amount_line = find_amount_line(text)
        if amount_line:
            data["amount_note"] = (
                f'The caption reports an amount but the number could not be read: "{amount_line}"'
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
