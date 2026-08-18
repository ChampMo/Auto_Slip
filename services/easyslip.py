import requests
import re
import logging
import time
from datetime import datetime, timedelta, timezone

from core.config import config

# เขตเวลาที่สลิปไทยหมายถึง ใช้เฉพาะตอนที่ EasySlip ไม่ได้ติดเขตเวลามากับเวลาโอน
THAI_TZ = timezone(timedelta(hours=7), "UTC+7")

# ─────────────────────────────────────────────────────────────────────────────
# บัญชีรับเงินของบริษัท : ชื่อที่ใช้ในชีท -> เลขบัญชีเต็ม
#
# EasySlip ส่งเลขบัญชีผู้รับมาแบบปิดบังบางส่วน เช่น "xxx-x-x5678-x"
# และ "ตำแหน่ง" ที่เปิดให้เห็นไม่เหมือนกันทุกธนาคาร บางเจ้าโชว์ 4 ตัวท้าย
# บางเจ้าโชว์ 4 ตัวก่อนตัวสุดท้าย จึงจะนับตำแหน่งตายตัวไม่ได้
#
# เก็บ "เลขเต็ม" ไว้แล้วทาบกันทีละตำแหน่ง จึงถูกต้องเสมอไม่ว่าธนาคารจะปิดบังตรงไหน
# ใส่ขีดคั่นหรือเว้นวรรคได้ ระบบตัดให้เอง
#
# 🔧 วิธีเพิ่ม/แก้บัญชี: แก้ตารางนี้ที่เดียว แล้วรีสตาร์ทบอท
#    ทั้งการจับคู่อัตโนมัติและปุ่มให้เลือกบัญชี ใช้ตารางนี้ร่วมกัน
# ─────────────────────────────────────────────────────────────────────────────

# ใส่ค่านี้ไว้เมื่อยังไม่รู้เลขบัญชีจริง — บัญชีนั้นจะยังขึ้นให้เลือกด้วยมือได้
# แต่จะไม่ถูกจับคู่อัตโนมัติ (กันไม่ให้เลขสมมติไปตรงกับสลิปจริงเข้า)
UNKNOWN_ACCOUNT = ""

# เรียงตามลำดับในชีทบัญชีของบริษัท เพื่อให้ตรวจทานเทียบกันได้ทีละบรรทัด
# (ลำดับในตารางนี้ไม่มีผลต่อการทำงาน ปุ่มเลือกบัญชีเรียงตามตัวอักษรเสมอ)
COMPANY_ACCOUNTS = {
    "SCB-CP":      "284-252-511-1",     # 1
    "BAY-CKB":     "778-157-9298",      # 2
    "KB-CKB":      "227-292-2872",      # 3
    "KB-CKB97726": "234-849-7726",      # 4
    "KKP-Jak":     "208-623-9349",      # 5
    "GSB-Jak":     "020-481-91980-9",   # 6
    "BBL-Ploy":    "158-418-130-7",     # 7
    "GSB-Ativit":  "020-470-2987-77",   # 8
    "KKP-LS":      "205-260-2984",      # 9
    "TTB-Nat":     "924-758-0914",      # 10
    "GSB-Teera":   "020-487-546-523",   # 11
    "KB-BS":       "220-815-0009",      # 12
    "GSB-Yo":      "020-434-9388-72",   # 13
    "TTB-Yo":      "924-218-5149",      # 14
    "SCB-Yo":      "156-436-9057",      # 15
    "TTB-Jak":     "919-256-0010",      # 16
    "SCB-MT20234": "284-252-0234",      # 17
    "KB-CP37446":  "227-293-7446",      # 18
    "KB-CP05761":  "234-850-5761",      # 19
    "GSB-Mon":     "020-324-245-305",   # 20
    "BAY-Mon":     "450-185-7393",      # 21
}

BANK_DROPDOWN_VALUES = sorted(COMPANY_ACCOUNTS)

# ตัวอักษรที่ธนาคาร/EasySlip ใช้ปิดบังหลักที่ไม่เปิดเผย
_MASK_CHARS = "xX*#"


# ─────────────────────────────────────────────────────────────────────────────
# ชื่อเจ้าของบัญชี — ด่านที่สองของการจับคู่บัญชีผู้รับ
#
# ⚠️ เลขบัญชีที่ EasySlip เปิดเผยมีแค่ไม่กี่หลัก บัญชีคนละธนาคารคนละคน
# จึงบังเอิญตรงกันได้จริง (เจอมาแล้ว: สลิปเข้าบัญชีคนอื่นแต่ 4 ตัวท้ายชนกับของเรา)
# ถ้าดูแต่ตัวเลขอย่างเดียว เงินที่ไม่ได้เข้าบริษัทจะถูกรับเข้าชีท
#
# 🔧 วิธีใช้: ใส่ชื่อเจ้าของบัญชีตามที่ธนาคารแสดงบนสลิป (พิมพ์แค่บางส่วนก็ได้
#    เช่นนามสกุล หรือชื่อบริษัทแบบย่อ ระบบเทียบแบบ "มีคำนี้อยู่ในชื่อไหม")
#    เว้นว่าง = ยังไม่ตรวจชื่อบัญชีนั้น (จับคู่ด้วยเลขอย่างเดียวเหมือนเดิม)
#
# หาชื่อจริงได้จากฐานข้อมูล ดูคำสั่ง SQL ใน scripts/check_account_owners.sql
# ─────────────────────────────────────────────────────────────────────────────
ACCOUNT_OWNERS = {
    # ชื่อเจ้าของบัญชีตามที่ธนาคารแสดงบนสลิป — ใส่ทั้งไทยและอังกฤษ
    # เพราะบางสลิปแสดงชื่ออังกฤษ และบางธนาคารย่อนามสกุลเหลือตัวเดียว
    #
    # ใส่แค่บางส่วนก็พอ ระบบเทียบทีละคำและยอมให้ชื่อบนสลิปถูกย่อ
    # เว้นว่าง = ยังไม่ตรวจบัญชีนั้น (จับคู่ด้วยเลขอย่างเดียวเหมือนเดิม)
    "SCB-CP":      ["บจก. ซีเพ้นท์ แอนด์ ออโต้", "C PAINT AND AUTO"],
    "BAY-CKB":     ["บจก. ซีเคบี 1314 ดิจิตอล", "CKB 1314 DIGITAL"],
    "KB-CKB":      ["บจก. ซีเคบี 1314 ดิจิตอล", "CKB 1314 DIGITAL"],
    "KB-CKB97726": ["บจก. ซีเคบี 1314 ดิจิตอล", "CKB 1314 DIGITAL"],
    "KKP-Jak":     ["จักกฤษ วอนเพียร", "Chakkrit Wonphian"],
    "GSB-Jak":     ["จักกฤษ วอนเพียร", "Chakkrit Wonphian"],
    "BBL-Ploy":    ["อุมาพร พงพันสถาพร", "Aumaporn Phongphansataporn"],
    "GSB-Ativit":  ["อติวิช ฉวีนาค", "Ativit Chahwinak"],
    "KKP-LS":      ["เลอสรร บุญลือ", "Loesan Bunlue"],
    "TTB-Nat":     ["ณัฐนิกา", "Nattanika Chipomja"],
    "GSB-Teera":   ["ธีระ สุเมธีวรากร", "Teera Sumeteewarakorn"],
    "KB-BS":       ["บจก. บิลท์สเปค", "BUILTSPAC"],
    "GSB-Yo":      ["อนุชา สังคะวาจารย์", "Anucha Sangkhawachan"],
    "TTB-Yo":      ["อนุชา สังคะวาจารย์", "Anucha Sangkhawachan"],
    "SCB-Yo":      ["อนุชา สังคะวาจารย์", "Anucha Sangkhawachan"],
    "TTB-Jak":     ["จักกฤษ วอนเพียร", "Chakkrit Wonphian"],
    "SCB-MT20234": ["บริษัท มีทอง การช่าง จำกัด", "MEETHONG KARNCHANG"],
    "KB-CP37446":  ["บจก. ซีเพ้นท์ แอนด์ ออโต้", "C PAINT AND AUTO"],
    "KB-CP05761":  ["บจก. ซีเพ้นท์ แอนด์ ออโต้", "C PAINT AND AUTO"],
    # "Montri" เพิ่มไว้เพราะ มนตรี สะกดอังกฤษได้สองแบบ ถ้าธนาคารใช้แบบไหนก็ผ่าน
    "GSB-Mon":     ["มนตรี กัมปนาทโกศล", "Montiri Kumpanatkosol", "Montri Kumpanatkosol"],
    "BAY-Mon":     ["มนตรี กัมปนาทโกศล", "Montiri Kumpanatkosol", "Montri Kumpanatkosol"],
}


# ─────────────────────────────────────────────────────────────────────────────
# ธนาคารเจ้าของบัญชี — ด่านที่สามของการจับคู่บัญชีผู้รับ
#
# เลขที่ EasySlip เปิดเผยมีไม่กี่หลัก บัญชี "คนละธนาคาร" จึงทาบกันติดได้ง่ายมาก
# ถ้าเทียบธนาคารด้วย เลขที่บังเอิญชนกันข้ามธนาคารจะถูกคัดออกทันที
#
# ธนาคารที่เราไม่ได้ถือบัญชี (กรุงไทย ธ.ก.ส. ฯลฯ) ก็ต้องรู้จักด้วย
# ไม่งั้นจะกลายเป็น "ไม่รู้จักธนาคารนี้" แล้วปล่อยผ่าน ซึ่งคือช่องโหว่ที่จะปิด
# ─────────────────────────────────────────────────────────────────────────────

ACCOUNT_BANKS = {
    "SCB-CP":      "SCB",
    "BAY-CKB":     "BAY",
    "KB-CKB":      "KBANK",
    "KB-CKB97726": "KBANK",
    "KKP-Jak":     "KKP",
    "GSB-Jak":     "GSB",
    "BBL-Ploy":    "BBL",
    "GSB-Ativit":  "GSB",
    "KKP-LS":      "KKP",
    "TTB-Nat":     "TTB",
    "GSB-Teera":   "GSB",
    "KB-BS":       "KBANK",
    "GSB-Yo":      "GSB",
    "TTB-Yo":      "TTB",
    "SCB-Yo":      "SCB",
    "TTB-Jak":     "TTB",
    "SCB-MT20234": "SCB",
    "KB-CP37446":  "KBANK",
    "KB-CP05761":  "KBANK",
    "GSB-Mon":     "GSB",
    "BAY-Mon":     "BAY",
}

# ชื่อ/รหัสที่ธนาคารกับ EasySlip ใช้เรียกตัวเอง -> รหัสกลางที่เราใช้
# ใส่ทั้งรหัสตัวเลข ตัวย่ออังกฤษ และชื่อไทย เพราะแต่ละสลิปส่งมาไม่เหมือนกัน
_BANK_ALIASES = {
    # ธนาคารที่เราถือบัญชีอยู่
    "scb": "SCB", "014": "SCB", "siamcommercialbank": "SCB", "ไทยพาณิชย์": "SCB",
    "kbank": "KBANK", "004": "KBANK", "kasikornbank": "KBANK", "กสิกรไทย": "KBANK",
    "bay": "BAY", "025": "BAY", "krungsri": "BAY", "bankofayudhya": "BAY",
    "กรุงศรีอยุธยา": "BAY",
    "kkp": "KKP", "069": "KKP", "kiatnakinphatra": "KKP", "kiatnakin": "KKP",
    "เกียรตินาคินภัทร": "KKP", "เกียรตินาคิน": "KKP",
    "gsb": "GSB", "030": "GSB", "governmentsavingsbank": "GSB", "ออมสิน": "GSB",
    "bbl": "BBL", "002": "BBL", "bangkokbank": "BBL", "กรุงเทพ": "BBL",
    "ttb": "TTB", "011": "TTB", "tmbthanachart": "TTB", "tmb": "TTB",
    "ทหารไทยธนชาต": "TTB", "ทีเอ็มบีธนชาต": "TTB",
    # ธนาคารที่เราไม่ได้ถือบัญชี — รู้จักไว้เพื่อฟันธงได้ว่า "คนละธนาคารแน่ๆ"
    "ktb": "KTB", "006": "KTB", "krungthai": "KTB", "กรุงไทย": "KTB",
    "baac": "BAAC", "034": "BAAC", "ธกส": "BAAC", "เพื่อการเกษตร": "BAAC",
    "ghb": "GHB", "033": "GHB", "อาคารสงเคราะห์": "GHB",
    "cimb": "CIMB", "022": "CIMB",
    "uob": "UOB", "024": "UOB",
    "tisco": "TISCO", "067": "TISCO", "ทิสโก้": "TISCO",
    "lhbank": "LHB", "073": "LHB", "แลนด์แอนด์เฮ้าส์": "LHB",
    "icbc": "ICBC", "070": "ICBC",
    "citi": "CITI", "017": "CITI",
    "sc": "SCBT", "020": "SCBT", "standardchartered": "SCBT",
    "isbt": "IBANK", "066": "IBANK", "อิสลาม": "IBANK",
    "truemoney": "TRUEMONEY", "tmn": "TRUEMONEY", "ทรูมันนี่": "TRUEMONEY",
}


def normalize_bank_key(value) -> str:
    """แปลงชื่อ/รหัสธนาคารที่ได้จากสลิป ให้เป็นรหัสกลาง คืน "" ถ้าไม่รู้จัก

    รับได้ทั้ง dict ({'id','name','short'}) และสตริงเดี่ยว
    """
    if isinstance(value, dict):
        for field in ("short", "id", "name"):
            found = normalize_bank_key(value.get(field))
            if found:
                return found
        return ""

    text = re.sub(r"[^0-9A-Za-zก-๙]", "", str(value or "")).lower()
    if not text:
        return ""
    text = text.replace("ธนาคาร", "").replace("จำกัดมหาชน", "")

    if text in _BANK_ALIASES:
        return _BANK_ALIASES[text]
    # รหัสตัวเลขที่ตัด 0 นำหน้าออกมาแล้ว เช่น '4' -> '004'
    if text.isdigit():
        padded = text.zfill(3)
        if padded in _BANK_ALIASES:
            return _BANK_ALIASES[padded]
    # ชื่อเต็มยาวๆ เช่น 'ธนาคารกสิกรไทยจำกัดมหาชน' ให้จับจากคำที่ยาวพอจะไม่กำกวม
    for alias, key in _BANK_ALIASES.items():
        if len(alias) >= 5 and alias in text:
            return key
    return ""


def bank_agrees(account_name: str, receiver_bank) -> bool | None:
    """สลิปใบนี้เป็นธนาคารเดียวกับบัญชีที่จับคู่ได้ไหม

    คืน None เมื่อ "ตอบไม่ได้" (ไม่รู้จักธนาคารที่สลิปส่งมา หรือยังไม่ได้จดว่า
    บัญชีนี้อยู่ธนาคารไหน) ผู้เรียกต้องไม่เอา None ไปตัดสินว่าไม่ตรง
    """
    expected = ACCOUNT_BANKS.get(account_name, "")
    if not expected:
        return None
    found = normalize_bank_key(receiver_bank)
    if not found:
        return None
    return found == expected


# คำนำหน้า/คำต่อท้ายที่ไม่ใช่ตัวชื่อ ตัดทิ้งก่อนเทียบ
_OWNER_TITLES = {
    "นาย", "นาง", "นางสาว", "นส", "ดร", "ด.ญ", "ดญ", "ดช",
    "บจก", "บมจ", "หจก", "บริษัท", "ห้างหุ้นส่วนจำกัด", "จำกัด", "มหาชน",
    "mr", "mrs", "miss", "ms", "dr",
    "co", "ltd", "company", "limited", "public", "corp", "inc",
}

_OWNER_DOTTED = ("น.ส.", "ด.ญ.", "ด.ช.", "บจก.", "บมจ.", "หจก.", "(มหาชน)")


def owner_tokens(value: str) -> list:
    """หั่นชื่อเป็นคำๆ ตัดคำนำหน้าออก แล้วทำให้เทียบง่าย

    'นาย เลอสรร บ'   -> ['เลอสรร', 'บ']
    'บจก. บิลท์สเปค'  -> ['บิลท์สเปค']
    'MR. LOESAN B'   -> ['loesan', 'b']
    """
    text = str(value or "")
    for dotted in _OWNER_DOTTED:
        text = text.replace(dotted, " ")
    parts = re.split(r"[^0-9A-Za-zก-๙]+", text)
    return [p.lower() for p in parts if p and p.lower() not in _OWNER_TITLES]


def normalize_owner(value: str) -> str:
    """ชื่อแบบติดกันไม่มีตัวคั่น ใช้ตอนเทียบหลวมๆ และตอนเขียน log"""
    return "".join(owner_tokens(value))


def _token_compatible(expected: str, found: str) -> bool:
    """คำสองคำนี้เป็นคำเดียวกันได้ไหม โดยยอมให้ฝั่งใดฝั่งหนึ่งถูกย่อ

    ธนาคารไทยย่อนามสกุลเหลือตัวเดียวบ่อยมาก ('เลอสรร บ') จึงเทียบแบบ
    'ตัวที่สั้นกว่าเป็นตัวขึ้นต้นของอีกตัวไหม' ไม่ใช่เทียบให้เท่ากันเป๊ะ
    """
    return expected.startswith(found) or found.startswith(expected)


def _alias_matches(alias: str, receiver_name: str) -> bool:
    expected = owner_tokens(alias)
    found = owner_tokens(receiver_name)
    if not expected or not found:
        return False
    pairs = list(zip(expected, found))
    return all(_token_compatible(exp, got) for exp, got in pairs)


def owner_matches(account_name: str, receiver_name: str) -> bool:
    """ชื่อผู้รับบนสลิป ตรงกับเจ้าของบัญชีที่เราจดไว้ไหม

    คืน True เมื่อ 'ยังไม่ได้จดชื่อไว้' ด้วย เพื่อไม่ให้บัญชีที่ยังไม่ได้กรอกชื่อ
    ถูกส่งเข้าแมนนวลทั้งหมดทันทีที่เพิ่มด่านนี้เข้ามา

    คืน True เมื่อ 'สลิปไม่มีชื่อผู้รับ' ด้วย (เช่นวอลเล็ตบางเจ้า) เพราะไม่มีอะไรให้เทียบ
    ไม่ใช่หลักฐานว่าผิดคน
    """
    configured = ACCOUNT_OWNERS.get(account_name, "")
    aliases = [configured] if isinstance(configured, str) else list(configured or [])
    aliases = [a for a in aliases if str(a).strip()]
    if not aliases:
        return True
    if not owner_tokens(receiver_name):
        return True
    return any(_alias_matches(alias, receiver_name) for alias in aliases)


def normalize_account_value(value: str) -> str:
    """ตัดตัวคั่นทิ้ง เหลือเฉพาะหลักของเลขบัญชี (ตัวเลข หรือ x ที่แทนหลักที่ถูกปิด)

    'xxx-x-x5678-x' -> 'xxxxx5678x'   (ยาว 10 เท่าเลขบัญชีจริง)
    '284-252-511-1' -> '2842525111'
    """
    kept = re.sub(rf"[^0-9{_MASK_CHARS}]", "", str(value or ""))
    return "".join("x" if ch in _MASK_CHARS else ch for ch in kept)


def parse_transfer_time(value: str):
    """แปลงเวลาโอนที่ EasySlip ส่งมาเป็น datetime ที่มีเขตเวลาติดมาด้วย

    '2026-08-08T19:04:20+07:00' -> datetime 19:04:20 +07:00

    คืน None ถ้าอ่านไม่ได้ ให้ปลายทางไปถามคนเอา ดีกว่าเดาเวลาผิดลงชีท
    """
    text = str(value or "").strip()
    if not text:
        return None

    # Python รุ่นเก่ารับ 'Z' ไม่ได้ แปลงเป็น +00:00 ก่อน
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.warning("Could not read the transfer time from the slip | value=%r", value)
        return None

    # ไม่มีเขตเวลาติดมา = ถือว่าเป็นเวลาไทย ซึ่งเป็นสิ่งที่สลิปไทยหมายถึงเสมอ
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=THAI_TZ)
    return parsed


def match_company_account(api_account_value: str) -> tuple[str | None, str]:
    """หาว่าเลขบัญชีผู้รับที่ EasySlip ส่งมา ตรงกับบัญชีบริษัทใบไหน

    คืน (ชื่อบัญชีในชีท, เหตุผล) โดยชื่อจะเป็น None ถ้าฟันธงไม่ได้

    วิธีเทียบ: เอาเลขที่เปิดเผยมาทาบกับเลขบัญชีเต็มของเรา "ตำแหน่งต่อตำแหน่ง"
    จึงไม่ต้องรู้ล่วงหน้าว่าธนาคารไหนปิดบังตรงไหน และไม่พลาดเวลา API ส่งเลขเต็มมา
    """
    mask = normalize_account_value(api_account_value)
    if not mask:
        return None, "no_account"

    visible = [(index, digit) for index, digit in enumerate(mask) if digit.isdigit()]
    if not visible:
        return None, "fully_masked"

    # ความยาวต้องเท่ากันถึงจะทาบตำแหน่งกันได้ (บัญชีไทยมีทั้งแบบ 10 และ 12 หลัก)
    matched = {
        name
        for name, account in known_company_accounts().items()
        if len(account) == len(mask)
        and all(account[index] == digit for index, digit in visible)
    }

    if len(matched) == 1:
        return matched.pop(), "matched"
    if matched:
        # เปิดเผยน้อยเกินไปจนชี้ไม่ได้ว่าใบไหน — ห้ามเดา ให้คนตัดสิน
        return None, "ambiguous"

    return None, "not_company"


def known_company_accounts() -> dict:
    """บัญชีที่รู้เลขจริงแล้ว (ชื่อ -> เลขล้วน) ใช้จับคู่อัตโนมัติได้

    บัญชีที่ยังไม่รู้เลขจะไม่อยู่ในนี้ จึงไม่มีทางถูกจับคู่พลาด
    แต่ยังขึ้นในปุ่มให้เลือกด้วยมือได้ตามปกติ
    """
    accounts = {}
    for name, raw in COMPANY_ACCOUNTS.items():
        digits = re.sub(r"\D", "", str(raw or ""))
        if digits:
            accounts[name] = digits
    return accounts


def validate_company_accounts() -> list[str]:
    """ตรวจความสอดคล้องของตารางบัญชี เรียกตอนบูตเพื่อให้รู้ปัญหาแต่เนิ่นๆ

    เงินเข้าบัญชีที่ตั้งค่าผิดจะถูกปฏิเสธอัตโนมัติแบบเงียบๆ จึงต้องดักไว้ก่อน
    """
    problems = []
    accounts = known_company_accounts()

    pending = sorted(set(COMPANY_ACCOUNTS) - set(accounts))
    if pending:
        problems.append(
            "ยังไม่ได้ใส่เลขบัญชีของ: " + ", ".join(pending)
            + " (เลือกด้วยมือได้ แต่จับคู่อัตโนมัติไม่ได้)"
        )

    used_by = {}
    for name, digits in accounts.items():
        used_by.setdefault(digits, []).append(name)
    for digits, names in sorted(used_by.items()):
        if len(names) > 1:
            problems.append(f"เลขบัญชี {digits} ซ้ำกันระหว่าง {', '.join(sorted(names))}")

    for name, digits in sorted(accounts.items()):
        if len(digits) < 10:
            problems.append(f"{name}: เลขบัญชี {digits} สั้นผิดปกติ ({len(digits)} หลัก)")

    return problems


logger = logging.getLogger(__name__)


def _parse_emv_tlv(payload: str) -> dict[str, str]:
    """Parse a simple EMV-style TLV payload into a flat tag/value map."""
    parsed: dict[str, str] = {}
    if not payload:
        return parsed

    index = 0
    payload_length = len(payload)
    while index + 4 <= payload_length:
        tag = payload[index:index + 2]
        length_text = payload[index + 2:index + 4]

        if not tag.isdigit() or not length_text.isdigit():
            break

        value_length = int(length_text)
        start = index + 4
        end = start + value_length
        if end > payload_length:
            break

        parsed[tag] = payload[start:end]
        index = end

    return parsed


def extract_amount_from_qr_payload(qr_payload: str) -> float | None:
    """Try to read the transfer amount directly from the QR payload."""
    if not qr_payload:
        return None

    tags = _parse_emv_tlv(qr_payload)
    amount_text = (tags.get("54") or "").strip()
    if not amount_text:
        return None

    try:
        return float(amount_text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def get_receiver_account_value(recv_acc: dict) -> str:
    """เลขบัญชีผู้รับดิบๆ ตามที่ EasySlip ส่งมา (ยังมีตัวปิดบังและตัวคั่นอยู่)"""
    if not isinstance(recv_acc, dict):
        return ""

    bank_info = recv_acc.get("bank") or {}
    if not isinstance(bank_info, dict):
        return ""

    return str(bank_info.get("account", "") or "").strip()


def describe_receiver_account(recv_acc: dict) -> str:
    """เลขบัญชีผู้รับแบบย่อ ไว้แสดงในข้อความและ log ตอนจับคู่ไม่ได้

    โชว์เฉพาะหลักที่ EasySlip เปิดเผยมาอยู่แล้ว ไม่ได้เปิดเผยเพิ่ม
    """
    account_value = get_receiver_account_value(recv_acc)
    if account_value:
        return account_value

    bank_info = recv_acc.get("bank") or {} if isinstance(recv_acc, dict) else {}
    code = str(bank_info.get("code", "") or "").strip() if isinstance(bank_info, dict) else ""
    return code


# ธนาคารยังยืนยันรายการไม่เสร็จ (slip_pending) เกิดกับสลิปที่เพิ่งโอนไม่กี่วินาที
# รอแป๊บเดียวแล้วถามใหม่มักได้คำตอบ จึงลองเองก่อนที่จะไปรบกวนคน
PENDING_RETRIES = 2          # ยิงเพิ่มอีก 2 ครั้งหลังครั้งแรก
PENDING_WAIT_SECONDS = 6     # ห่างกันครั้งละ 6 วินาที


def verify_slip(qr_payload: str) -> dict:
    """ตรวจสลิปกับ EasySlip พร้อมลองซ้ำถ้าธนาคารยังยืนยันไม่เสร็จ

    ฟังก์ชันนี้ถูกเรียกจาก worker thread (asyncio.to_thread) การ sleep ตรงนี้
    จึงไม่ได้แช่แข็งบอท สลิปใบอื่นยังเดินต่อได้ตามปกติ
    """
    result = _verify_slip_once(qr_payload)

    for attempt in range(1, PENDING_RETRIES + 1):
        if result.get("success") or result.get("error") != "SLIP_PENDING":
            return result
        logger.info(
            "Slip still pending at the bank, retrying | attempt=%s/%s | wait=%ss",
            attempt, PENDING_RETRIES, PENDING_WAIT_SECONDS,
        )
        time.sleep(PENDING_WAIT_SECONDS)
        result = _verify_slip_once(qr_payload)

    if result.get("error") == "SLIP_PENDING":
        logger.warning("Slip still pending after %s tries — handing it to a person",
                       PENDING_RETRIES + 1)
    return result


def _verify_slip_once(qr_payload: str) -> dict:
    # URL ตาม Document (v1/verify)
    url = "https://api.easyslip.com/v1/verify" 
    
    headers = {
        "Authorization": f"Bearer {config.EASYSLIP_API_KEY}"
    }
    
    # ส่งเป็น params ตามตัวอย่างในหน้าเว็บ
    params = {
        "payload": qr_payload
    }
    
    try:
        # ต้องมี timeout เสมอ ไม่งั้นถ้า EasySlip ค้าง จะลากทั้งบอทค้างตามไปด้วย
        # (connect 10 วิ, read 30 วิ)
        response = requests.get(url, headers=headers, params=params, timeout=(10, 30))
        result = response.json()
        payload_amount = extract_amount_from_qr_payload(qr_payload)
        logger.info(
            "EasySlip verify response | payload_amount=%s | status=%s | message=%s",
            payload_amount,
            result.get("status"),
            result.get("message", ""),
        )
        
        # เช็ค status จาก API ว่าเท่ากับ 200 หรือไม่ (ตาม Document)
        if result.get("status") == 200:
            data = result.get("data", {})
            
            # โครงสร้าง Amount
            amount_data = data.get("amount", {})
            if isinstance(amount_data, dict):
                amount = amount_data.get("amount", 0.0)
            else:
                amount = amount_data
            
            # ดึงชื่อคนโอน — บางธนาคารส่งมาแต่ชื่ออังกฤษ หรือส่ง th มาเป็นค่าว่าง
            # ใช้ `or` แทน get(default) เพื่อให้ค่าว่างตกไปใช้ชื่ออังกฤษแทน
            sender = "Unknown"
            if "sender" in data and "account" in data["sender"] and "name" in data["sender"]["account"]:
                name_data = data["sender"]["account"]["name"] or {}
                sender = name_data.get("th") or name_data.get("en") or "ไม่ระบุชื่อ"
            
            # 👇 --- เพิ่มโค้ดชุดนี้สำหรับดึงข้อมูล "ผู้รับ" ---
            receiver_info = "-"
            receiver_bank_code = ""
            receiver_bank_matches = False
            # ชี้ได้ชัดไหมว่าเป็นบัญชีบริษัทหรือไม่ใช่ — คนละเรื่องกับ "ตรงหรือไม่ตรง"
            # ถ้าสลิปปิดบังเลขจนชี้ไม่ได้ ต้องให้คนดู ห้ามปฏิเสธอัตโนมัติ
            receiver_bank_resolved = False
            # เหตุผลว่าทำไมถึงตัดสินแบบนั้น ใช้เขียนข้อความให้คนกดปุ่มอ่านเข้าใจ
            # ถ้าบอกเหตุผลผิด คนกดจะตัดสินผิดตาม — ด่านสุดท้ายคือคน
            receiver_reason = "no_account"
            receiver_owner_name = ""
            receiver_bank_label = ""
            receiver_candidate = ""
            if "receiver" in data and "account" in data["receiver"]:
                recv_acc = data["receiver"]["account"]
                raw_account = get_receiver_account_value(recv_acc)
                receiver_bank_code = describe_receiver_account(recv_acc)
                # ชื่อผู้รับตามที่ธนาคารแสดง ใช้ยืนยันว่าเลขที่ตรงกันเป็นบัญชีเดียวกันจริง
                name_block = recv_acc.get("name") if isinstance(recv_acc, dict) else None
                if isinstance(name_block, dict):
                    receiver_owner_name = name_block.get("th") or name_block.get("en") or ""

                # ธนาคารของผู้รับ อยู่คนละที่กับเลขบัญชี (receiver.bank ไม่ใช่ receiver.account)
                receiver_bank_block = data["receiver"].get("bank") if isinstance(
                    data.get("receiver"), dict) else None
                receiver_bank_label = normalize_bank_key(receiver_bank_block)

                # ทาบเลขที่ API เปิดเผย กับเลขบัญชีเต็มของบริษัท ทีละตำแหน่ง
                matched_name, match_reason = match_company_account(raw_account)
                receiver_reason = match_reason
                receiver_candidate = matched_name or ""

                bank_verdict = bank_agrees(matched_name, receiver_bank_block) if matched_name else None

                if matched_name and bank_verdict is False:
                    # เลขทาบติดแต่คนละธนาคาร = คนละบัญชีแน่นอน เลขบังเอิญชนกันเฉยๆ
                    # ยังไม่ปฏิเสธเอง เพราะอาจเป็นเราที่จดธนาคารของบัญชีไว้ผิด
                    logger.warning(
                        "Account digits matched but the bank does not | account=%s "
                        "| matched=%s (%s) | slip bank=%s",
                        raw_account, matched_name, ACCOUNT_BANKS.get(matched_name, "?"),
                        receiver_bank_label or "unknown",
                    )
                    receiver_reason = "bank_mismatch"
                    receiver_info = receiver_bank_code or matched_name
                    matched_name = None

                elif matched_name and not owner_matches(matched_name, receiver_owner_name):
                    # เลขตรงแต่ชื่อเจ้าของไม่ตรง = คนละบัญชีที่เลขบังเอิญชนกัน
                    # ห้ามรับเข้าเอง และห้ามปฏิเสธเอง เพราะอาจเป็นเราที่จดชื่อไว้ผิด
                    logger.warning(
                        "Account digits matched but the owner name does not | account=%s "
                        "| matched=%s | receiver=%s",
                        raw_account, matched_name, receiver_owner_name,
                    )
                    receiver_reason = "owner_mismatch"
                    receiver_info = receiver_bank_code or matched_name
                    matched_name = None

                elif matched_name:
                    receiver_info = matched_name
                    receiver_bank_matches = True
                    receiver_bank_resolved = True
                    receiver_reason = "matched"

                elif match_reason in ("ambiguous", "fully_masked"):
                    # API เปิดเผยน้อยเกินกว่าจะชี้ได้ว่าบัญชีไหน — ห้ามเดา ให้คนตัดสิน
                    logger.warning(
                        "Cannot identify receiver account | reason=%s | account=%s",
                        match_reason, raw_account,
                    )
                    receiver_info = receiver_bank_code or "-"

                elif raw_account:
                    # อ่านเลขได้ครบและไม่ตรงกับบัญชีบริษัทใบไหนเลย = ชี้ชัดว่าไม่ใช่ของเรา
                    logger.info("Receiver is not a company account | account=%s", raw_account)
                    receiver_info = receiver_bank_code
                    receiver_bank_resolved = True

                # ไม่มีเลขบัญชีเลย (เช่น ทรูมันนี่) ให้ดึง "ชื่อ" มาแทน
                elif isinstance(recv_acc, dict) and isinstance(recv_acc.get("name"), dict):
                    receiver_info = recv_acc["name"].get("th") or recv_acc["name"].get("en") or "-"
            # 👆 -----------------------------------------
                
            return {
                "success": True,
                "amount": float(amount),
                "payload_amount": payload_amount,
                "sender": sender,
                "receiver": receiver_info,  # 👈 ส่งค่าที่ดึงได้กลับไปด้วย
                "receiver_bank_code": receiver_bank_code,
                "receiver_bank_matches": receiver_bank_matches,
                "receiver_bank_resolved": receiver_bank_resolved,
                "receiver_reason": receiver_reason,
                "receiver_owner_name": receiver_owner_name,
                "receiver_bank_label": receiver_bank_label,
                "receiver_candidate": receiver_candidate,
                # เวลาที่โอนจริงตามสลิป ไม่ใช่เวลาที่บอทตรวจ — ใช้เขียนช่อง Time ในชีท
                "transfer_at": parse_transfer_time(data.get("date")),
                "raw_data": data
            }
        else:
            # Convert API error messages into user-friendly English messages
            raw_error_msg = result.get("message", "").upper()
            status_code = result.get("status")

            if "QUOTA" in raw_error_msg or "EXCEEDED" in raw_error_msg:
                user_friendly_msg = "Quota exceeded or EasySlip package expired. Please renew your subscription."
                error_type = "QUOTA_EXCEEDED"
            elif "UNAUTHORIZED" in raw_error_msg or status_code == 401:
                user_friendly_msg = "Authentication failed (invalid EasySlip API key)."
                error_type = "UNAUTHORIZED"
            elif "NOT FOUND" in raw_error_msg or "INVALID" in raw_error_msg:
                user_friendly_msg = "Slip not found in bank records, or QR code is invalid (possible fake slip)."
                error_type = "INVALID_SLIP"
            elif "PENDING" in raw_error_msg:
                user_friendly_msg = (
                    "The bank has not confirmed this transfer yet. "
                    "This usually clears within a minute — send the slip again shortly."
                )
                error_type = "SLIP_PENDING"
            elif "MAINTENANCE" in raw_error_msg:
                user_friendly_msg = "Verification service or source bank is under maintenance. Please try again later."
                error_type = "MAINTENANCE"
            else:
                user_friendly_msg = f"Slip verification API error (message: {result.get('message')})"
                error_type = "UNKNOWN_ERROR"

            return {
                "success": False,
                "error": error_type,
                "user_message": user_friendly_msg,
                "payload_amount": payload_amount,
            }
            
    except requests.exceptions.Timeout:
        logger.warning("EasySlip verify timed out | payload_amount=%s", extract_amount_from_qr_payload(qr_payload))
        return {
            "success": False,
            "error": "TIMEOUT",
            "user_message": "Slip verification timed out. The EasySlip service did not respond in time.",
            "payload_amount": extract_amount_from_qr_payload(qr_payload),
        }

    except Exception as e:
        logger.exception("EasySlip verify exception | payload_amount=%s", extract_amount_from_qr_payload(qr_payload))
        return {
            "success": False,
            "error": "EXCEPTION",
            "user_message": f"Network or server error during slip verification: {str(e)}",
            "payload_amount": extract_amount_from_qr_payload(qr_payload),
        }