import asyncio
import os
import logging
import time
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ContextTypes
from core.captions import (
    find_standalone_numbers,
    parse_typed_amount,
    parse_typed_date_and_times,
)
from core.config import config
from core.scanner import file_sha256, read_qr_code
from bot.keyboards import (
    get_add_qr_keyboard,
    get_approval_keyboard,
    get_bank_selection_keyboard,
    get_qr_help_keyboard,
)
from bot.verification_flow import VerificationDecision, determine_verification_action
from database.session import SessionLocal
from core.matcher import (
    NEEDS_QR_STATUS,
    PHOTO_KEY_PREFIX,
    photo_key,
    topic_allowed,
    process_incoming_slip,
)
from core.names import split_bank_name, split_name_variants, surname_initials
from services.easyslip import verify_slip, extract_amount_from_qr_payload, BANK_DROPDOWN_VALUES
from database.crud import (
    add_audit_log,
    claim_transaction,
    get_approver_ids,
    is_sheet_locked,
    DECIDED_STATUSES,
    SHEET_REOPEN_ACTION,
)
from services.gsheets import (
    append_to_sheet,
    format_transfer_times,
    get_sheet_name_for_datetime,
    SheetEntry,
)
import json
from database.models import (
    NAMES_LIST_MAX,
    QR_REF_MAX,
    TRANSFER_TIME_MAX,
    WARNING_MAX,
    Transaction,
    UsedQR,
    clamp,
)
from telegram.error import TimedOut, NetworkError

logger = logging.getLogger(__name__)

# เวลารอรูปใบถัดไปของอัลบั้มเดียวกัน (Telegram ส่งมาติดๆ กันเป็นคนละ message)
MEDIA_GROUP_WAIT_SECONDS = 2.0
_media_groups: dict[str, dict] = {}
_media_group_lock = asyncio.Lock()

# คำขอให้แอดมินพิมพ์ยอดเอง เก็บไว้ในหน่วยความจำ (บอทรีสตาร์ทแล้วหาย ให้กด Receive ใหม่)
AMOUNT_REQUEST_TTL_SECONDS = 30 * 60
_amount_requests: dict[str, dict] = {}

# คำขอให้แอดมินพิมพ์เวลาโอนเอง ตอนที่อ่านเวลาจากสลิปไม่ได้ (อายุเท่ากับคำขอยอด)
_time_requests: dict[str, dict] = {}


def normalize_bank_name(value: str) -> str:
    first_name, last_name_initial = split_bank_name(value)
    return f"{first_name}{last_name_initial}"


def names_match_in_bank_format(api_name: str, reported_name: str) -> bool:
    api_first, _ = split_bank_name(api_name)
    reported_first, _ = split_bank_name(reported_name)

    if not api_first or not reported_first:
        return False
    if api_first != reported_first:
        return False

    # ถ้าฝั่งไหนไม่ได้ให้อักษรย่อนามสกุลมา (เช่น caption พิมพ์แค่ชื่อจริง) ให้เทียบแค่ชื่อจริงพอ
    api_initials = surname_initials(api_name)
    reported_initials = surname_initials(reported_name)
    if not api_initials or not reported_initials:
        return True

    # ตรงกับอักษรย่อที่เป็นไปได้ตัวใดตัวหนึ่งก็พอ — นามสกุลหลายคำอย่าง 'ณ ร้อยเอ็ด'
    # ธนาคารโชว์ 'ณ' แต่คำสุดท้ายขึ้นต้นด้วย 'ร' ถ้าดูแค่คำสุดท้ายจะไม่ตรงทั้งที่เป็นคนเดียวกัน
    return bool(api_initials & reported_initials)


def is_reported_name_verified(senders: list[str], reported_name: str) -> bool:
    """ข้อ 'ชื่อ' จะผ่านก็ต่อเมื่อเทียบกันได้จริงทั้งสองฝั่ง

    caption ไม่ได้พิมพ์ชื่อมา (format แบบ User) หรือ API อ่านชื่อผู้โอนไม่ได้
    ถือว่าไม่ผ่าน ต้องให้แอดมินกดยืนยันเอง

    แชทมักเขียนชื่อคนเดียวกันทั้งไทยและอังกฤษคั่นด้วย / — สลิปจะโชว์แค่แบบเดียว
    จึงถือว่าผ่านถ้าตรงกับแบบใดแบบหนึ่ง
    """
    reported = (reported_name or "").strip()
    if not reported or reported == "-":
        return False

    verified_senders = [sender for sender in senders if sender and sender.strip()]
    if not verified_senders:
        return False

    variants = split_name_variants(reported)
    return all(
        any(names_match_in_bank_format(sender, variant) for variant in variants)
        for sender in verified_senders
    )


def unlock_after_failed_save(db, txn) -> None:
    """เขียนลงชีทไม่สำเร็จ ให้กลับไปรอแอดมินกดใหม่ได้ ไม่ค้างสถานะไว้เฉยๆ"""
    txn.status = "pending"
    add_audit_log(db, txn.batch_id, SHEET_REOPEN_ACTION)


def unique_receiver_account(receivers: list[str]) -> str:
    """ถ้าทุกสลิปในชุดเข้าบัญชีเดียวกัน คืนชื่อบัญชีนั้น ถ้าคนละบัญชีคืนค่าว่าง"""
    unique_accounts = {receiver.strip() for receiver in receivers if receiver and receiver.strip()}
    if len(unique_accounts) == 1:
        return unique_accounts.pop()
    return ""


def resolve_known_bank_value(txn) -> str:
    """บัญชีผู้รับที่ระบบรู้อยู่แล้วจากการอ่านสลิป

    คืนค่าเฉพาะกรณีที่จับคู่กับบัญชีบริษัทใน COMPANY_ACCOUNTS ได้จริง
    (ค่าที่ไม่ตรงกับตัวเลือกในชีท เช่น เลขบัญชีดิบหรือชื่อร้าน ให้ถือว่ายังไม่รู้ ต้องถามแอดมิน)
    """
    for value in [txn.receiver_account, txn.chat_bank]:
        cleaned = (value or "").strip()
        if cleaned in BANK_DROPDOWN_VALUES:
            return cleaned
    return ""


def build_bank_mismatch_reason(bank_codes: list[str], bank_matches: list[bool]) -> str:
    """อธิบายให้แอดมินเข้าใจว่าทำไมบัญชีผู้รับไม่ผ่าน (เลี่ยงศัพท์ในโค้ด)"""
    mismatched_codes = [code for code, matched in zip(bank_codes, bank_matches) if code and not matched]
    if mismatched_codes:
        return f"account ending in {', '.join(mismatched_codes)} is not a company account"
    if bank_codes:
        return "the receiver account is not a company account"
    return "could not read the receiver account from the slip"


def amounts_match(expected_amount: float | None, actual_amount: float | None) -> bool:
    if expected_amount is None or actual_amount is None:
        return False
    return round(float(expected_amount), 2) == round(float(actual_amount), 2)


def format_amount(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f}"


def format_thb(value: float | None) -> str:
    """ยอดเงินสำหรับแสดงในแชท ให้ติดหน่วยไปด้วยเสมอ"""
    if value is None:
        return "unknown"
    return f"{float(value):.2f} THB"


# คำอธิบายสถานะสำหรับแสดงให้คนอ่าน ใช้ร่วมกันทั้งข้อความในกลุ่มและคำสั่งดูสถานะ
STATUS_ICONS = {
    "receive": "✅ Received",
    "reject": "❌ Rejected",
    "duplicate": "♻️ Marked as duplicate",
    "pending": "⏳ Waiting for a decision",
    "needs_qr": "🔍 Waiting for the QR code",
    "interrupted": "⚠️ Interrupted while saving — please send it again",
}


def message_link(chat_id, msg_id) -> str:
    """ลิงก์กระโดดไปข้อความต้นทาง (ใช้ได้กับ supergroup เท่านั้น)"""
    chat = str(chat_id)
    if chat.startswith("-100") and msg_id:
        return f"https://t.me/c/{chat[4:]}/{msg_id}"
    return ""


def describe_duplicate(original: dict | None) -> str:
    """ข้อความตอบกลับเมื่อสลิปใบนี้เคยส่งมาแล้ว

    ต้องบอกให้ได้ว่า "ใบเดิมจบยังไง" เพราะนั่นคือสิ่งที่คนส่งอยากรู้จริงๆ
    ถ้าบอกแค่ว่าซ้ำ เขาต้องไปไล่หาข้อความเก่าเอง ซึ่งหายากมากในกลุ่มที่คุยกันเยอะ
    """
    header = "⚠️ Duplicate slip\n\nThis slip has already been submitted, so it was not recorded again."
    if not original:
        return header

    lines = [header, ""]
    lines.append(f"The first one: {STATUS_ICONS.get(original['status'].lower(), original['status'])}")
    if original["id"] and original["id"] != "-":
        lines.append(f"• ID: {original['id']}")
    if original["amount"] is not None:
        lines.append(f"• Amount: {format_thb(original['amount'])}")
    if original["created_at"] is not None:
        # created_at เก็บเป็น UTC แบบไม่มี tzinfo ต้องบอกเขตเวลาก่อนแปลงเป็นวันธุรกิจ
        recorded = get_sheet_name_for_datetime(
            original["created_at"].replace(tzinfo=timezone.utc)
        )
        lines.append(f"• Sheet tab: {recorded}")

    link = message_link(original["chat_id"], original["msg_id"])
    if link:
        lines.append(f"\nThe original message: {link}")

    return "\n".join(lines)


def build_checks_from_record(txn) -> list[dict]:
    """สร้างผลตรวจ 3 ข้อจากข้อมูลที่เก็บไว้แล้ว ไม่ต้องยิงตรวจสลิปใหม่

    ใช้ตอน /recheck เพื่อให้เห็นภาพเดียวกับตอนที่ระบบตรวจครั้งแรก
    ถ้าโชว์แค่ค่าที่เก็บไว้เฉยๆ คนกดจะไม่รู้ว่าเดิมติดตรงไหน แล้วตัดสินยาก
    """
    slip_name = txn.sender_names or ""
    chat_name = (txn.chat_fullname or "").strip()
    slip_amount = txn.api_total_amount
    chat_amount = txn.chat_amount
    account = (txn.receiver_account or "").strip()

    return [
        {
            "title": "Name does not match",
            "short": "Name",
            "passed": is_reported_name_verified([slip_name], chat_name),
            "value": slip_name or "-",
            "slip": describe_reported(slip_name),
            "chat": describe_reported(chat_name),
        },
        {
            "title": "Amount does not match",
            "short": "Amount",
            "passed": amounts_match(chat_amount, slip_amount),
            "value": format_thb(slip_amount if slip_amount is not None else chat_amount),
            "slip": format_thb(slip_amount) if slip_amount is not None else "(not given)",
            "chat": format_thb(chat_amount) if chat_amount is not None else "(not given)",
        },
        {
            "title": "Receiver account is not ours",
            "short": "Account",
            "passed": account in BANK_DROPDOWN_VALUES,
            "value": account or "-",
            "reason": (f"{account} is not a company account" if account
                       else "the receiver account was never read from the slip"),
        },
    ]


def describe_reported(value: str) -> str:
    """ค่าที่ฝั่งใดฝั่งหนึ่งไม่ได้ให้มา ต้องเขียนให้ต่างจากค่าที่ให้มาแล้วแต่ว่าง"""
    cleaned = (value or "").strip()
    return cleaned if cleaned and cleaned != "-" else "(not given)"


def format_check_results(checks: list[dict]) -> list[str]:
    """เรียงผลตรวจโดยเอาข้อที่ติดขึ้นก่อน แล้วยุบข้อที่ผ่านเหลือบรรทัดเดียว

    ข้อที่ติดกางให้เห็นว่าสลิปกับแชทต่างกันตรงไหน เพราะนั่นคือสิ่งที่คนกดต้องตัดสิน
    ส่วนข้อที่ผ่านไม่ต้องการความสนใจ จึงไม่ควรกินพื้นที่เท่ากัน

    จงใจไม่จัดเป็นคอลัมน์แบบตาราง เพราะชื่อไทยมีสระกับวรรณยุกต์ที่ไม่กินความกว้าง
    เติมช่องว่างให้จำนวนตัวอักษรเท่ากันแล้ว ตายังเห็นไม่ตรงอยู่ดี — ใช้ขึ้นบรรทัดแทน
    """
    lines = []

    for check in [c for c in checks if not c["passed"]]:
        lines.append(f"❌ {check['title']}")
        if check.get("reason"):
            lines.append(f"   {check['reason']}")
        else:
            lines.append(f"   Slip: {check['slip']}")
            lines.append(f"   Chat: {check['chat']}")
        # ข้อที่ติดกินหลายบรรทัด ต้องเว้นวรรคคั่นไม่ให้อ่านปนกัน
        lines.append("")

    if lines:
        lines.pop()  # บรรทัดว่างท้ายสุดไม่ต้อง

    # ข้อที่ผ่านบรรทัดละข้อ มีไอคอนของตัวเอง กวาดตาแล้วเห็นครบทุกข้อทันที
    for check in [c for c in checks if c["passed"]]:
        lines.append(f"✅ {check['short']} {check['value']}")

    return lines


def describe_txn_amount(txn) -> str:
    """ยอดของรายการนี้ ใช้ยอดที่ตรวจจากสลิปก่อน ถ้าไม่มีค่อยใช้ยอดที่แจ้งในแชท"""
    return format_thb(txn.api_total_amount if txn.api_total_amount is not None else txn.chat_amount)


def summarize_qr_payload(qr_payload: str, keep: int = 18) -> str:
    cleaned = (qr_payload or "").strip()
    if len(cleaned) <= keep * 2:
        return cleaned
    return f"{cleaned[:keep]}...{cleaned[-keep:]}"


def sum_batch_amount(amounts: list[float | None]) -> tuple[float | None, bool]:
    """รวมยอดทุกใบในชุด คืน None ถ้าอ่านยอดไม่ได้สักใบ (ไม่ใช่ 0 เพราะจะทำให้เข้าใจผิด)"""
    known_amounts = [float(amount) for amount in amounts if amount is not None]
    missing = len(known_amounts) != len(amounts)
    if not known_amounts:
        return None, missing
    return sum(known_amounts), missing


def join_unique(values: list[str]) -> str:
    """รวมรายชื่อโดยตัดค่าซ้ำออก เช่น ['KKP-LS', 'KKP-LS'] -> 'KKP-LS'"""
    unique_values = []
    for value in values:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in unique_values:
            unique_values.append(cleaned)
    return ", ".join(unique_values)


def verify_slip_batch(qr_list: list[str], batch_id: str) -> dict:
    """ยิง API ตรวจสอบสลิปทุกใบในรูปเดียวกัน แล้วรวมผลลัพธ์เป็นก้อนเดียว

    ไม่ยุ่งกับฐานข้อมูลเลย เพราะฟังก์ชันนี้ถูกเรียกจาก worker thread
    (session ของ SQLAlchemy ใช้ข้ามเธรดไม่ได้) ผู้เรียกเอา raw_data ไปบันทึกเองทีหลัง
    """
    result = {
        "api_success": True,
        "amounts": [],
        "senders": [],
        "receivers": [],
        "bank_codes": [],
        "bank_matches": [],
        "bank_resolved": [],
        # เวลาโอนของแต่ละใบ ใบที่อ่านเวลาไม่ได้จะเป็น None
        "transfer_times": [],
        "raw_data_by_qr": {},
        "user_error_msg": "",
        "error_type": "",
    }

    for qr in qr_list:
        logger.info(
            "Start verifying QR | batch_id=%s | payload=%s | payload_amount=%s",
            batch_id,
            summarize_qr_payload(qr),
            format_amount(extract_amount_from_qr_payload(qr)),
        )
        api_result = verify_slip(qr)

        if api_result["success"]:
            logger.info(
                "Verify success | batch_id=%s | amount=%s | sender=%s | receiver=%s | bank_code=%s | bank_matches=%s",
                batch_id,
                format_amount(api_result.get("amount")),
                api_result.get("sender", ""),
                api_result.get("receiver", ""),
                api_result.get("receiver_bank_code", ""),
                api_result.get("receiver_bank_matches", False),
            )
            result["amounts"].append(api_result["amount"])
            result["senders"].append(api_result["sender"])
            result["receivers"].append(api_result["receiver"])
            result["bank_codes"].append(api_result.get("receiver_bank_code", ""))
            result["bank_matches"].append(api_result.get("receiver_bank_matches", False))
            result["bank_resolved"].append(api_result.get("receiver_bank_resolved", False))
            result["transfer_times"].append(api_result.get("transfer_at"))

            result["raw_data_by_qr"][qr] = json.dumps(api_result["raw_data"], ensure_ascii=False)
        else:
            logger.warning(
                "Verify failed | batch_id=%s | error=%s | user_message=%s | payload_amount=%s",
                batch_id,
                api_result.get("error", "UNKNOWN_ERROR"),
                api_result.get("user_message", ""),
                format_amount(api_result.get("payload_amount")),
            )
            result["api_success"] = False
            result["amounts"].append(api_result.get("payload_amount"))
            error_msg = api_result.get("error", "UNKNOWN_ERROR")
            result["error_type"] = error_msg
            result["user_error_msg"] = api_result.get(
                "user_message", f"⚠️ Slip verification system error ({error_msg})"
            )

    return result


async def send_manual_review_message(
    bot,
    chat_id: str,
    reply_to_message_id: int,
    batch_id: str,
    body_lines: list[str],
    footer_message: str,
    with_duplicate: bool = False,
    with_receive: bool = True,
    with_retry: bool = False,
):
    """ข้อความขอให้แอดมินตัดสิน: หัวข้อ -> รายละเอียด -> สิ่งที่ต้องทำต่อ"""
    alert_text = (
        "⚠️ Manual review required\n\n"
        + "\n".join(body_lines)
        + f"\n\n{footer_message}"
    )

    sent = await bot.send_message(
        chat_id=chat_id,
        reply_to_message_id=reply_to_message_id,
        text=alert_text,
        reply_markup=get_approval_keyboard(
            batch_id, with_duplicate=with_duplicate,
            with_receive=with_receive, with_retry=with_retry,
        ),
    )
    remember_review_message(batch_id, getattr(sent, "message_id", None))
    return sent


def remember_review_message(batch_id: str, review_msg_id) -> None:
    """จำว่าปุ่มอยู่บนข้อความไหน จะได้ตามไปปิดปุ่มได้ตอนสลิปถูกตัดสินแล้ว

    เก็บใน DB ไม่ใช่ในหน่วยความจำ เพราะปุ่มอยู่บนจอได้ข้ามการรีสตาร์ทบอท
    """
    if review_msg_id is None:
        return
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        if txn is not None:
            txn.review_msg_id = str(review_msg_id)
            db.commit()


async def close_review_buttons(bot, chat_id, review_msg_id, skip_msg_id=None) -> None:
    """ปิดปุ่มบนข้อความตรวจสอบใบเดิม หลังสลิปจบไปแล้ว

    ถ้าไม่ปิด ปุ่มชุดเดิมจะยังค้างให้กดได้ทั้งที่รายการจบแล้ว
    (กดไปก็ไม่เสียหาย เพราะมีด่านเช็คสถานะ แต่ทำให้คนในกลุ่มสับสน)
    """
    if not review_msg_id or bot is None:
        return
    if skip_msg_id is not None and str(review_msg_id) == str(skip_msg_id):
        return  # เป็นข้อความเดียวกับที่เพิ่งแก้ไป ปุ่มถูกเอาออกแล้ว

    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=int(review_msg_id), reply_markup=None
        )
    except Exception as exc:
        # ข้อความอาจถูกลบ หรือปุ่มถูกปิดไปแล้ว ไม่ใช่เรื่องคอขาดบาดตาย
        logger.info("Could not close review buttons | msg_id=%s | %s", review_msg_id, exc)


def pick_image_source(message):
    """เลือกไฟล์รูปจากข้อความ รองรับทั้งรูปปกติและรูปที่ส่งมาแบบไฟล์

    คนมักส่งสลิปแบบ 'ส่งเป็นไฟล์' เพื่อไม่ให้ภาพแตก ซึ่งมาเป็น document ไม่ใช่ photo
    """
    if getattr(message, "photo", None):
        return message.photo[-1]

    document = getattr(message, "document", None)
    if document is not None and str(getattr(document, "mime_type", "") or "").startswith("image/"):
        return document

    return None


async def download_and_scan_photo(message) -> tuple | None:
    """โหลดรูปจาก Telegram แล้วอ่าน QR ทุกใบในรูป

    คืน (รายการ QR, แฮชของไฟล์) หรือ None ถ้าโหลดรูปไม่สำเร็จ
    แฮชไว้ใช้กันสลิปซ้ำตอนที่อ่าน QR ไม่ออก
    """
    chat_id = message.chat_id
    msg_id = message.message_id
    photo = pick_image_source(message)
    if photo is None:
        logger.info("Message has no image to scan | chat_id=%s | msg_id=%s", chat_id, msg_id)
        return None
    temp_path = f"temp_{chat_id}_{msg_id}.img"

    try:
        photo_file = await photo.get_file(
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )

        await photo_file.download_to_drive(
            custom_path=temp_path,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )

    except TimedOut:
        await message.reply_text(
            "⚠️ Could not download the photo\n\n"
            "Telegram timed out. Please send the slip again."
        )
        return None

    except NetworkError:
        await message.reply_text(
            "⚠️ Could not download the photo\n\n"
            "Connection to Telegram failed. Please send the slip again."
        )
        return None

    try:
        qr_data_list = read_qr_code(temp_path)
        photo_hash = file_sha256(temp_path)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    logger.info("Photo scan result | chat_id=%s | msg_id=%s | qr_count=%s", chat_id, msg_id, len(qr_data_list))
    if not qr_data_list:
        print("📸 [DEBUG] No QR code detected in this image")
        return qr_data_list, photo_hash

    print(f"📸 [DEBUG] Detected {len(qr_data_list)} QR code(s):")
    for idx, qr in enumerate(qr_data_list, 1):
        logger.info(
            "QR payload summary | chat_id=%s | msg_id=%s | index=%s | amount=%s | payload=%s",
            chat_id,
            msg_id,
            idx,
            format_amount(extract_amount_from_qr_payload(qr)),
            summarize_qr_payload(qr),
        )
        print(f"   QR Code {idx} -> Payload: {qr}")

    return qr_data_list, photo_hash


async def collect_media_group_photo(media_group_id, bot, chat_id, msg_id, caption, qr_list,
                                    photo_hash=None):
    """พักรูปที่ส่งมาพร้อมกันในข้อความเดียว (อัลบั้ม) ไว้ก่อน แล้วค่อยประมวลผลรวมทีเดียว

    Telegram ส่งอัลบั้มมาเป็นคนละ message และติด caption มาแค่ใบเดียว
    ถ้าแยกกันประมวลผลจะกลายเป็นคนละรายการและใบที่ไม่มี caption จะไม่มียอดให้เทียบ
    """
    async with _media_group_lock:
        group = _media_groups.get(media_group_id)
        is_first_photo = group is None

        if is_first_photo:
            group = {
                "chat_id": chat_id,
                "msg_id": msg_id,
                "caption": caption,
                "qr_list": [],
                "photo_hashes": [],
                "photo_count": 0,
                "deadline": 0.0,
            }
            _media_groups[media_group_id] = group

        group["qr_list"].extend(qr_list)
        if photo_hash:
            group["photo_hashes"].append(photo_hash)
        group["photo_count"] += 1

        # ตอบกลับที่ใบที่ติด caption มา (ปกติคือใบแรก แต่ไม่เสมอไป)
        if caption and not group["caption"]:
            group["caption"] = caption
            group["msg_id"] = msg_id

        # ทุกครั้งที่มีรูปใหม่เข้ามา ให้เลื่อนเวลาปิดกลุ่มออกไป
        group["deadline"] = time.monotonic() + MEDIA_GROUP_WAIT_SECONDS

    if is_first_photo:
        asyncio.create_task(flush_media_group(media_group_id, bot))


async def flush_media_group(media_group_id, bot):
    """รอจนไม่มีรูปใหม่เข้ามาแล้ว ค่อยส่งทั้งอัลบั้มไปตรวจเป็นชุดเดียว"""
    try:
        while True:
            async with _media_group_lock:
                group = _media_groups.get(media_group_id)
                if group is None:
                    return

                remaining = group["deadline"] - time.monotonic()
                if remaining <= 0:
                    _media_groups.pop(media_group_id, None)
                    break

            await asyncio.sleep(remaining)

        logger.info(
            "Media group collected | media_group_id=%s | photos=%s | qr_count=%s",
            media_group_id,
            group["photo_count"],
            len(group["qr_list"]),
        )

        if not group["qr_list"]:
            print(f"📸 [DEBUG] Album of {group['photo_count']} photo(s) has no QR code")

        await process_slip_group(
            bot,
            group["chat_id"],
            group["msg_id"],
            group["caption"],
            group["qr_list"],
            group["photo_count"],
            group["photo_hashes"],
        )
    except Exception:
        logger.exception("Failed to process media group | media_group_id=%s", media_group_id)


async def flush_pending_media_groups(bot):
    """เคลียร์อัลบั้มที่ยังค้างในคิวก่อนบอทปิดตัว

    ปกติอัลบั้มจะรอ 2 วินาทีให้รูปมาครบ ถ้าบอทถูกสั่งปิดในจังหวะนั้นพอดี
    รูปชุดนั้นจะหายไปเงียบๆ — เรียกตัวนี้ตอน shutdown เพื่อประมวลผลให้จบก่อน
    """
    async with _media_group_lock:
        pending = list(_media_groups.items())
        _media_groups.clear()

    if not pending:
        return

    logger.info("Flushing %s pending media group(s) before shutdown", len(pending))
    for media_group_id, group in pending:
        if not group["qr_list"]:
            logger.warning(
                "Dropping album with no QR at shutdown | media_group_id=%s | photos=%s",
                media_group_id, group["photo_count"],
            )
            continue
        try:
            await process_slip_group(
                bot, group["chat_id"], group["msg_id"], group["caption"],
                group["qr_list"], group["photo_count"], group["photo_hashes"],
            )
        except Exception:
            logger.exception("Failed to flush media group at shutdown | media_group_id=%s", media_group_id)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    chat_id = message.chat_id
    print(f"👉 [DEBUG] Received a message from the group with Chat ID: {chat_id}")
    msg_id = message.message_id
    caption = message.caption or ""

    # กลุ่มที่ตั้งหมายเลขหัวข้อไว้ ให้ทำงานเฉพาะหัวข้อนั้น หัวข้ออื่นข้ามไปเงียบๆ
    if not topic_allowed(chat_id, getattr(message, "message_thread_id", None)):
        logger.info("Ignored photo from another topic | chat_id=%s | topic=%s",
                    chat_id, getattr(message, "message_thread_id", None))
        return

    # รูป QR ที่ตอบกลับข้อความทวง แทบไม่มีใครใส่ caption มาด้วย
    # จึงต้องรู้ก่อนว่าเป็นคำตอบของคำถามไหม ก่อนจะไปเจอด่านกรอง caption ข้างล่าง
    replied = getattr(message, "reply_to_message", None)
    replied_id = getattr(replied, "message_id", None)
    awaiting = load_waiting_slip(chat_id, replied_id) if replied_id else None

    # รูปเดี่ยวที่ไม่มีข้อความกำกับ ตัดทิ้งตั้งแต่ยังไม่โหลดไฟล์
    # (อัลบั้มตัดตรงนี้ไม่ได้ เพราะ Telegram ใส่ caption มาแค่ใบเดียว ต้องรอดูทั้งชุดก่อน)
    if awaiting is None and not message.media_group_id and not has_usable_caption(caption):
        logger.info("Ignored photo with no caption | chat_id=%s | msg_id=%s", chat_id, msg_id)
        return

    # รูปที่ตอบกลับข้อความของบอทแต่ไม่มีสลิปรออยู่แล้ว (เช่นรูปที่สองของอัลบั้ม
    # ที่รูปแรกเพิ่งไปต่อให้เรียบร้อย) ต้องทิ้ง ไม่ใช่เอาไปสร้างเป็นสลิปใบใหม่
    if awaiting is None and replied_id is not None and not has_usable_caption(caption):
        logger.info("Ignored a reply photo with nothing waiting | chat_id=%s | msg_id=%s",
                    chat_id, msg_id)
        return

    scanned = await download_and_scan_photo(message)
    if scanned is None:
        return
    qr_data_list, photo_hash = scanned

    # ตอบกลับข้อความที่บอททวง QR ไว้ = ใบนี้คือ QR ของสลิปใบเดิม ไม่ใช่สลิปใบใหม่
    if awaiting is not None:
        await handle_qr_photo_reply(context.bot, message, qr_data_list, awaiting)
        return

    # หลายรูปในข้อความเดียว: รอให้ครบทั้งอัลบั้มก่อน
    if message.media_group_id:
        await collect_media_group_photo(
            str(message.media_group_id), context.bot, chat_id, msg_id, caption, qr_data_list,
            photo_hash,
        )
        return

    await process_slip_group(context.bot, chat_id, msg_id, caption, qr_data_list,
                             photo_count=1, photo_hashes=[photo_hash])


def has_usable_caption(caption: str) -> bool:
    """รูปที่ไม่มีข้อความกำกับ ถือว่าไม่ใช่การส่งสลิป

    ในกลุ่มมีการแชร์ภาพหน้าจอกันเป็นปกติ ซึ่งบางทีก็มี QR ของสลิปติดอยู่ในภาพด้วย
    ถ้าไม่กรองออก บอทจะไปสร้างรายการจากภาพพวกนั้นแล้วจบลงที่แถวขยะในชีท
    """
    return bool((caption or "").strip())


# เว็บอ่าน QR ที่แนะนำให้ใช้ตอนต้องพิมพ์รหัสมาเอง
QR_READER_URL = "https://qrcodescan.in/"


def build_qr_request_text(photo_count: int, reported: dict) -> str:
    """ข้อความทวง QR — ต้องบอกวิธีให้ครบ เพราะคนส่งสลิปไม่ใช่คนเขียนโปรแกรม"""
    photo_word = "this photo" if photo_count == 1 else f"these {photo_count} photos"
    lines = [
        "🔍 QR code needed",
        "",
        f"No QR code could be read from {photo_word}, so this slip cannot be "
        "checked against the bank yet. Nothing has been recorded.",
        "",
        "Details reported in this chat:",
        f"• ID: {reported['id']}",
        f"• Name: {reported['name'] or '-'}",
        f"• Amount: {format_thb(reported['amount'])}",
        "",
        "Reply to THIS message with either one:",
        "",
        "1. A picture of just the QR code — open the slip, zoom in on the QR "
        "and take a screenshot of that part alone.",
        "",
        f"2. The QR code as text — open {QR_READER_URL}, upload the slip, "
        "copy the long code it gives you and paste it here.",
    ]
    if reported["warning"]:
        lines.insert(3, f"⚠️ {reported['warning']}\n")
    return "\n".join(lines)


async def ask_for_qr(bot, chat_id, msg_id, batch_id: str, photo_count: int, reported: dict) -> None:
    """ขอ QR จากคนส่ง แล้วจำไว้ว่าคำถามนี้เป็นของสลิปใบไหน

    เก็บ id ของข้อความคำถามลงฐานข้อมูล ไม่ใช่หน่วยความจำ เพราะสลิปจะค้างรอ
    จนกว่าจะมีคนตอบ ซึ่งอาจข้ามการรีสตาร์ทบอทไป
    """
    question = await bot.send_message(
        chat_id=chat_id,
        reply_to_message_id=msg_id,
        text=build_qr_request_text(photo_count, reported),
        reply_markup=get_qr_help_keyboard(batch_id),
    )

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        if txn is not None:
            txn.qr_request_msg_id = str(question.message_id)
        add_audit_log(db, batch_id, "qr_requested")
        db.commit()

    logger.info("Asked for the QR code | batch_id=%s | photos=%s", batch_id, photo_count)


def find_slip_awaiting_qr(db, chat_id, question_msg_id):
    """หาสลิปที่กำลังรอ QR จากข้อความคำถามที่ถูกตอบกลับมา"""
    if question_msg_id is None:
        return None
    return (
        db.query(Transaction)
        .filter(
            Transaction.chat_id == str(chat_id),
            Transaction.qr_request_msg_id == str(question_msg_id),
            Transaction.status == NEEDS_QR_STATUS,
        )
        .first()
    )


async def resume_slip_with_qr(bot, txn_row: dict, qr_list: list) -> bool:
    """เอา QR ที่เพิ่งได้มา ต่อยอดสลิปใบที่ค้างอยู่ให้ตรวจต่อได้

    ใช้ caption ของข้อความต้นฉบับ ไม่ใช่ของข้อความที่ส่ง QR มา
    เพราะยอดกับชื่อที่ต้องเทียบอยู่ในข้อความแรก
    """
    if not qr_list:
        return False

    with SessionLocal() as db:
        # QR ที่เคยอ่านได้ของชุดนี้อยู่ใน used_qrs อยู่แล้ว ต้องเอามารวม ไม่ใช่ใช้แทน
        # ไม่งั้นชุดที่มีสองใบจะเหลือใบเดียว แล้วยอดรวมจะขาดไปหนึ่งใบเงียบๆ
        known = [row.qr_ref for row in db.query(UsedQR)
                 .filter(UsedQR.batch_id == txn_row["batch_id"]).all()
                 if not row.qr_ref.startswith(PHOTO_KEY_PREFIX)]
        add_audit_log(db, txn_row["batch_id"], "qr_supplied_by_hand")
        # ล้างทิ้งหลังใช้เสร็จ ไม่ให้คำถามเก่าถูกตอบซ้ำได้อีก
        waiting_row = db.query(Transaction).filter(
            Transaction.batch_id == txn_row["batch_id"]).first()
        if waiting_row is not None:
            waiting_row.qr_request_msg_id = None
        db.commit()

    merged = list(dict.fromkeys(known + qr_list))
    logger.info(
        "Received a QR for a waiting slip | batch_id=%s | already_known=%s | now=%s",
        txn_row["batch_id"], len(known), len(merged),
    )

    await process_slip_group(
        bot, txn_row["chat_id"], txn_row["msg_id"], txn_row["caption"],
        merged, photo_count=1, photo_hashes=[txn_row["photo_hash"]],
        force_batch_id=txn_row["batch_id"],
    )
    return True


def load_waiting_slip(chat_id, question_msg_id) -> dict | None:
    """คัดข้อมูลของสลิปที่รอ QR ออกมาก่อนปิด session"""
    with SessionLocal() as db:
        txn = find_slip_awaiting_qr(db, chat_id, question_msg_id)
        if txn is None:
            return None
        return {
            "batch_id": txn.batch_id,
            "chat_id": txn.chat_id,
            "msg_id": txn.msg_id,
            "caption": txn.raw_caption or "",
            "photo_hash": txn.photo_hash,
        }


QR_NOT_ALLOWED_TEXT = (
    "⚠️ Only approvers can answer this\n\n"
    "The QR decides whether the money is confirmed with the bank, so it has to come "
    "from someone who is allowed to approve slips. Ask an approver to send it."
)


def may_answer_qr_request(user) -> bool:
    """คนที่ตอบคำขอ QR ได้ = คนที่มีสิทธิ์อนุมัติเท่านั้น

    QR คือสิ่งที่ตัดสินว่าเงินก้อนนี้ยืนยันกับธนาคารได้หรือไม่ ถ้าใครก็ยัดเข้ามาได้
    เท่ากับเปิดให้คนนอกป้อนหลักฐานเข้าสู่เส้นทางที่จบด้วยการลงชีท
    """
    return user is not None and str(user.id) in load_approver_ids()


async def handle_qr_photo_reply(bot, message, qr_list: list, waiting: dict) -> bool:
    """รูปที่ตอบกลับข้อความทวง QR — คืน True ถ้าจัดการให้แล้ว

    ผู้เรียกหา waiting มาให้แล้ว เพราะต้องรู้ตั้งแต่ก่อนโหลดไฟล์ว่าเป็นคำตอบไหม
    """
    if not may_answer_qr_request(getattr(message, "from_user", None)):
        await message.reply_text(QR_NOT_ALLOWED_TEXT)
        return True

    if not qr_list:
        await message.reply_text(
            "⚠️ Still no QR code in that picture\n\n"
            "Crop tighter so the whole QR square fills the picture, and make sure "
            "it is not blurred. You can also paste the QR code as text instead — "
            f"read it at {QR_READER_URL}"
        )
        return True

    await resume_slip_with_qr(bot, waiting, qr_list)
    return True


async def handle_qr_text_reply(message) -> bool:
    """ข้อความที่ตอบกลับคำถามเรื่อง QR — คืน True ถ้าเป็นคำตอบของคำถามนี้"""
    waiting = load_waiting_slip(
        message.chat_id, getattr(message.reply_to_message, "message_id", None)
    )
    if waiting is None:
        return False

    if not may_answer_qr_request(getattr(message, "from_user", None)):
        await message.reply_text(QR_NOT_ALLOWED_TEXT)
        return True

    payload = (message.text or "").strip()
    if not looks_like_qr_payload(payload):
        await message.reply_text(
            "⚠️ That does not look like a QR code\n\n"
            "The code is one long line with no spaces, usually starting with 00 "
            f"and around 100–200 characters. Read it at {QR_READER_URL} then paste "
            "the whole thing here — or reply with a picture of just the QR instead."
        )
        return True

    await resume_slip_with_qr(message.get_bot(), waiting, [payload])
    return True


def looks_like_qr_payload(text: str) -> bool:
    """กรองข้อความที่ไม่มีทางเป็น payload ของ QR สลิป

    ไม่ตรวจลึกถึงโครงสร้าง EMV เพราะถ้าพิมพ์มาผิดจริง EasySlip จะปฏิเสธเองอยู่แล้ว
    ตรงนี้แค่กันข้อความคุยเล่นไม่ให้ถูกส่งไปตรวจ
    """
    cleaned = (text or "").strip()
    # ยาวเกินคอลัมน์ qr_ref = INSERT ล้มตอนบันทึก แล้วสลิปจะหายไปเงียบๆ
    # ต้องกันตั้งแต่ตรงนี้ ไม่ใช่ปล่อยให้ไปตายที่ฐานข้อมูล
    if not (40 <= len(cleaned) <= QR_REF_MAX):
        return False
    if " " in cleaned or "\n" in cleaned:
        return False
    return cleaned.isalnum()


def describe_possible_duplicates(batch_id: str, amount) -> list:
    """เตือนถ้ามีรายการอื่นยอดเท่ากันในวันธุรกิจเดียวกัน

    ใช้กับใบที่ไม่มี QR เท่านั้น เพราะใบที่มี QR ถูกกันซ้ำด้วย QR ไปแล้ว
    เตือนอย่างเดียว ไม่บล็อก — ลูกค้าคนละคนโอนยอดเท่ากันในวันเดียวกันเกิดขึ้นได้จริง
    เทียบเวลาโอนแบบเป๊ะนาทีจะทำตอนที่แอดมินกรอกวันและเวลาเข้ามาแล้ว
    """
    if amount is None:
        return []

    from bot.commands import business_day_range_utc

    start_utc, end_utc = business_day_range_utc()
    with SessionLocal() as db:
        others = (
            db.query(Transaction)
            .filter(
                Transaction.batch_id != batch_id,
                Transaction.chat_amount == amount,
                Transaction.created_at >= start_utc,
                Transaction.created_at < end_utc,
                Transaction.status.notin_(["Reject", "reject", NEEDS_QR_STATUS]),
            )
            .all()
        )
        found = [(t.chat_trans_id or t.chat_user_id or "-", message_link(t.chat_id, t.msg_id))
                 for t in others]

    if not found:
        return []

    lines = ["", f"⚠️ Possible duplicate — {len(found)} other slip(s) today "
                 f"have the same amount:"]
    for slip_id, link in found[:5]:
        lines.append(f"• ID {slip_id}{' — ' + link if link else ''}")
    lines.append("Check that this is not the same transfer before receiving it.")
    return lines


async def process_slip_group(bot, chat_id, msg_id, caption: str, qr_list: list[str],
                             photo_count: int, photo_hashes: list = None,
                             allow_without_qr: bool = False,
                             force_batch_id: str = None):
    """ตรวจสลิปทั้งชุด (1 รูป 1 QR, 1 รูปหลาย QR หรือหลายรูปในข้อความเดียว) เป็นรายการเดียว

    แบ่ง DB session เป็นช่วงสั้นๆ ไม่ถือ connection ค้างระหว่างรอ EasySlip หรือ Google
    ซึ่งรวมกันอาจกินเวลาหลายสิบวินาทีต่อสลิปหนึ่งชุด
    """
    if not has_usable_caption(caption):
        logger.info(
            "Ignored photo with no caption | chat_id=%s | msg_id=%s | photos=%s | qr_count=%s",
            chat_id, msg_id, photo_count, len(qr_list),
        )
        return

    # สลิปใบเดียวกันที่ถูกส่งซ้ำในชุดเดียวกัน ต้องนับแค่ครั้งเดียว ไม่งั้นยอดรวมจะเบิ้ล
    qr_data_list = list(dict.fromkeys(qr_list))
    if len(qr_data_list) != len(qr_list):
        logger.info(
            "Removed duplicated QR in the same group | chat_id=%s | msg_id=%s | %s -> %s",
            chat_id, msg_id, len(qr_list), len(qr_data_list),
        )

    # ── ช่วงที่ 1: รับเข้าระบบ + เช็คซ้ำ ─────────────────────────────
    reported = None
    original = None
    with SessionLocal() as db:
        status, txn = process_incoming_slip(
            db, qr_data_list, chat_id, msg_id, caption,
            photo_hashes=photo_hashes, allow_without_qr=allow_without_qr,
            force_batch_id=force_batch_id,
        )
        if txn is not None:
            # คัดค่าออกมาก่อนปิด session จะได้ใช้ต่อได้โดยไม่ต้องถือ connection ไว้
            reported = {
                "batch_id": txn.batch_id,
                "amount": txn.chat_amount,
                "name": (txn.chat_fullname or "").strip(),
                "id": txn.chat_trans_id or txn.chat_user_id or "-",
                "warning": txn.caption_warning or "",
            }
            # ใบซ้ำต้องบอกให้ได้ว่าใบเดิมจบยังไง ไม่งั้นคนส่งต้องไปไล่หาเอง
            original = {
                "status": str(txn.status),
                "amount": txn.api_total_amount if txn.api_total_amount is not None else txn.chat_amount,
                "id": txn.chat_trans_id or txn.chat_user_id or "-",
                "chat_id": txn.chat_id,
                "msg_id": txn.msg_id,
                "created_at": txn.created_at,
            }

    if status == "unknown_group":
        logger.warning(
            "Ignored photo from unregistered group | chat_id=%s | msg_id=%s "
            "(set VIP_WE_CHAT_ID / VIP_12_CHAT_ID in .env)",
            chat_id, msg_id,
        )
        return

    if status == "duplicate":
        await bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=msg_id,
            text=describe_duplicate(original),
        )
        return

    batch_id = reported["batch_id"]

    # ── อ่าน QR ไม่ออก = ยังยืนยันกับธนาคารไม่ได้ ต้องขอ QR ก่อน ──
    #
    # เดิมปล่อยให้แอดมินกดรับจากข้อมูลในแชทได้เลย ซึ่งแปลว่าเงินก้อนนั้นเข้าชีท
    # โดยไม่เคยถูกตรวจกับธนาคารสักครั้ง และไม่มีตัวกันสลิปซ้ำด้วย
    if status == "needs_qr":
        await ask_for_qr(bot, chat_id, msg_id, batch_id, photo_count, reported)
        return

    # QR เสียจริงและมีคนยืนยันแล้ว = ตรวจกับธนาคารไม่ได้ ต้องให้คนตัดสินจากข้อมูลในแชท
    if not qr_data_list:
        with SessionLocal() as db:
            add_audit_log(db, batch_id, "manual_review_no_qr")

        logger.info(
            "No QR (declared unreadable), sending for manual review | batch_id=%s "
            "| reported_name=%s | reported_amount=%s",
            batch_id, reported["name"] or "-", format_amount(reported["amount"]),
        )

        no_qr_lines = [
            "The QR code on this slip was reported unreadable, so it could not be "
            "checked against the bank. Everything below comes from the message only.",
        ]
        if reported["warning"]:
            no_qr_lines.append(f"⚠️ {reported['warning']}")
        no_qr_lines.extend([
            "",
            "Details reported in this chat:",
            f"• ID: {reported['id']}",
            f"• Name: {reported['name'] or '-'}",
            f"• Amount: {format_thb(reported['amount'])}",
        ])
        duplicate_lines = describe_possible_duplicates(batch_id, reported["amount"])
        no_qr_lines.extend(duplicate_lines)

        await send_manual_review_message(
            bot, chat_id, msg_id, batch_id, no_qr_lines,
            "Receive will ask for the transfer date and time, then the bank account.",
            with_duplicate=bool(duplicate_lines),
        )
        return

    # ── ช่วงที่ 2: ยิง API ตรวจสลิป (ไม่ถือ session และไม่แช่แข็ง event loop) ──
    print(f"🔎 Verifying {len(qr_data_list)} slip(s) from {photo_count} photo(s) via API...")
    batch_result = await asyncio.to_thread(verify_slip_batch, qr_data_list, batch_id)

    api_success = batch_result["api_success"]
    verified_total_amount, payload_amount_missing = sum_batch_amount(batch_result["amounts"])
    sender_names_str = join_unique(batch_result["senders"])
    receiver_names_str = join_unique(batch_result["receivers"])

    expected_amount = reported["amount"]
    chat_name = reported["name"]
    amount_match = amounts_match(expected_amount, verified_total_amount)
    # หลาย QR ในรูปเดียว หรือหลายรูปในข้อความเดียว = ให้แอดมินตัดสินเสมอ ไม่ auto receive
    multi_slip_batch = len(qr_data_list) > 1 or photo_count > 1
    is_name_match = is_reported_name_verified(batch_result["senders"], chat_name)
    bank_matches = all(batch_result["bank_matches"]) if batch_result["bank_matches"] else False
    # ทุกใบต้องชี้ได้ชัด ถึงจะยอมให้ปฏิเสธอัตโนมัติเรื่องบัญชี
    bank_resolved = all(batch_result["bank_resolved"]) if batch_result["bank_resolved"] else False

    # ไม่มีป้ายบอกยอด แต่ในข้อความมีตัวเลขลอยๆ ที่ตรงกับยอดในสลิปพอดี
    # น่าจะเป็นยอดที่เขาแจ้งแบบไม่ใส่ป้าย — ห้ามปฏิเสธอัตโนมัติ ต้องให้คนยืนยัน
    # จงใจไม่เอาไปนับว่า "ผ่าน" เพราะยืนยันไม่ได้ว่าตัวเลขนั้นคือยอดจริง
    if (expected_amount is None and verified_total_amount is not None
            and not reported["warning"]
            and verified_total_amount in find_standalone_numbers(caption)):
        reported["warning"] = (
            f"The message has no amount label, but it contains "
            f"{format_amount(verified_total_amount)} which matches the slip. "
            "Confirm it is the deposit amount before saving."
        )
        logger.info(
            "Unlabelled number matches the slip amount | batch_id=%s | amount=%s",
            batch_id, format_amount(verified_total_amount),
        )

    logger.info(
        "Batch verification summary | batch_id=%s | api_success=%s | payload_missing=%s "
        "| verified_total=%s | expected_amount=%s | amount_match=%s | name_match=%s | bank_match=%s",
        batch_id, api_success, payload_amount_missing,
        format_amount(verified_total_amount), format_amount(expected_amount),
        amount_match, is_name_match, bank_matches,
    )

    verification_decision = determine_verification_action(
        api_success=api_success,
        amount_matches=amount_match,
        name_matches=is_name_match,
        bank_matches=bank_matches,
        bank_resolved=bank_resolved,
        multi_slip_batch=multi_slip_batch,
        caption_unreliable=bool(reported["warning"]),
        # ทุกใบในชุดต้องอ่านเวลาได้ ไม่งั้นช่อง Time จะขาดของบางใบไป
        transfer_time_known=bool(batch_result["transfer_times"])
        and all(t is not None for t in batch_result["transfer_times"]),
    )

    # ── ช่วงที่ 3: บันทึกผลตรวจและตัดสิน ─────────────────────────────
    sheet_entry = None
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        if txn is None:
            logger.error("Transaction disappeared before saving results | batch_id=%s", batch_id)
            return

        for qr, raw_data in batch_result["raw_data_by_qr"].items():
            used_qr = db.query(UsedQR).filter(UsedQR.qr_ref == qr).first()
            if used_qr:
                used_qr.api_raw_data = raw_data

        txn.api_total_amount = verified_total_amount
        # หลายสลิปในชุดเดียวทำให้ชื่อต่อกันยาวได้มาก ตัดให้พอดีคอลัมน์ก่อนบันทึก
        txn.sender_names = clamp(sender_names_str, NAMES_LIST_MAX)
        txn.receiver_names = clamp(receiver_names_str, NAMES_LIST_MAX)
        # คำเตือนอาจถูกเพิ่มหลังตรวจสลิปเสร็จ (เช่นเจอตัวเลขลอยที่ตรงกับยอด)
        # ต้องเก็บลงด้วย ไม่งั้น /status กับ /recheck จะไม่เห็นเหตุผลที่ต้องให้คนดู
        txn.caption_warning = clamp(reported["warning"] or None, WARNING_MAX)

        # เวลาโอนตามสลิป — เก็บทั้งแบบข้อความ (ไว้เขียนชีท) และแบบ datetime (ไว้ query)
        # อ่านไม่ได้สักใบก็ปล่อยว่างไว้ แล้วไปถามแอดมินตอนกด Receive
        transfer_moments = [t for t in batch_result["transfer_times"] if t is not None]
        if transfer_moments:
            txn.transfer_time_text = clamp(
                format_transfer_times(batch_result["transfer_times"]), TRANSFER_TIME_MAX
            )
            # เก็บใบแรกสุดเป็น UTC แบบไม่มี tzinfo ให้เทียบกับ created_at ได้ตรงๆ
            txn.transfer_at = min(transfer_moments).astimezone(timezone.utc).replace(tzinfo=None)

        if api_success:
            # ทุกสลิปเข้าบัญชีเดียวกัน -> ใช้บัญชีนั้นได้เลย ไม่ต้องถามแอดมินซ้ำ
            txn.receiver_account = unique_receiver_account(batch_result["receivers"]) or receiver_names_str

        if verification_decision == VerificationDecision.AUTO_RECEIVE:
            txn.status = "Receive"
            add_audit_log(db, batch_id, "api_verified_matched")
            sheet_entry = SheetEntry.from_transaction(txn)
        elif verification_decision == VerificationDecision.AUTO_REJECT:
            txn.status = "Reject"
            add_audit_log(db, batch_id, "auto_rejected_mismatch")
        else:
            add_audit_log(db, batch_id, "manual_review_required")

        receiver_account = txn.receiver_account

    # ── ช่วงที่ 4: ลงมือตามผลตัดสิน ─────────────────────────────────
    if verification_decision == VerificationDecision.AUTO_RECEIVE:
        sheet_success, sheet_error_msg = await asyncio.to_thread(append_to_sheet, sheet_entry)

        with SessionLocal() as db:
            if sheet_success:
                add_audit_log(db, batch_id, "sheet_saved")
            else:
                txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
                if txn is not None:
                    # ปลดล็อกให้กดบันทึกใหม่ได้ หลังแก้ปัญหาชีทเรียบร้อย
                    unlock_after_failed_save(db, txn)

        if sheet_success:
            logger.info(
                "✅ Auto-Received: Batch=%s | Receiver(s): %s | Account: %s | Amount: %s",
                batch_id[:15], receiver_names_str, receiver_account or "-", verified_total_amount,
            )
            await bot.send_message(
                chat_id=chat_id,
                reply_to_message_id=msg_id,
                text=(f"✅ Received automatically\n\n"
                      f"ID: {reported['id']}\n"
                      f"Sender: {sender_names_str or '-'}\n"
                      f"Account: {receiver_account or '-'}\n"
                      f"Amount: {format_thb(verified_total_amount)}\n\n"
                      f"Saved to today's sheet."),
            )
        else:
            await send_manual_review_message(
                bot, chat_id, msg_id, batch_id,
                [
                    "All checks passed, but the slip could not be saved to today's sheet.",
                    "",
                    f"Reason: {str(sheet_error_msg)[:200]}",
                    "",
                    f"Sender: {sender_names_str or '-'}",
                    f"Account: {receiver_account or '-'}",
                    f"Amount: {format_thb(verified_total_amount)}",
                ],
                "Fix the sheet, then press Receive to save it again.",
            )
        return

    if verification_decision == VerificationDecision.AUTO_REJECT:
        reject_reasons = []
        if not bank_matches:
            reject_reasons.append(
                "• Receiver account: "
                + build_bank_mismatch_reason(batch_result["bank_codes"], batch_result["bank_matches"])
            )
        if not amount_match:
            reject_reasons.append(
                f"• Amount: the slip says {format_thb(verified_total_amount)}, "
                f"but this chat reported {format_thb(expected_amount)}"
            )

        logger.info(
            "❌ Auto-Rejected: Batch=%s | reasons=%s",
            batch_id[:15], " / ".join(reject_reasons) or "unknown",
        )
        await bot.send_message(
            chat_id=chat_id,
            reply_to_message_id=msg_id,
            text=("❌ Rejected automatically\n\n"
                  + ("\n".join(reject_reasons) or "• The slip details do not match this chat")
                  + "\n\nNothing was saved to the sheet. "
                    "Correct the details and send the slip again."),
        )
        return

    # --- Manual review: ข้อมูลไม่พอตัดสิน หรือติดแค่เรื่องชื่อ ---
    body_lines = []
    if reported["warning"]:
        body_lines.append(f"⚠️ {reported['warning']}")
    if not api_success:
        body_lines.append(f"Slip verification failed: {batch_result['user_error_msg']}")
    if multi_slip_batch:
        slip_word = "slip" if len(qr_data_list) == 1 else "slips"
        photo_word = "photo" if photo_count == 1 else "photos"
        body_lines.append(
            f"{len(qr_data_list)} {slip_word} in {photo_count} {photo_word}, "
            f"totalling {format_thb(verified_total_amount)}. "
            "Multiple slips are never received automatically."
        )
    if body_lines:
        body_lines.append("")

    body_lines.extend(format_check_results([
        {
            "title": "Name does not match",
            "short": "Name",
            "passed": is_name_match,
            "value": sender_names_str or "-",
            "slip": describe_reported(sender_names_str),
            "chat": describe_reported(chat_name),
        },
        {
            "title": "Amount does not match",
            "short": "Amount",
            "passed": amount_match,
            "value": format_thb(verified_total_amount),
            "slip": describe_reported(format_amount(verified_total_amount)) + " THB"
                    if verified_total_amount is not None else "(not given)",
            "chat": describe_reported(format_amount(expected_amount)) + " THB"
                    if expected_amount is not None else "(not given)",
        },
        {
            "title": "Receiver account is not ours",
            "short": "Account",
            "passed": bank_matches,
            "value": receiver_names_str or "-",
            "reason": build_bank_mismatch_reason(
                batch_result["bank_codes"], batch_result["bank_matches"]
            ),
        },
    ]))

    if verified_total_amount is None and expected_amount is None:
        footer_message = (
            "No amount is known for this slip — Receive will ask you to type it in. "
            "Reject discards it."
        )
    else:
        footer_message = "Choose Receive to save it to today's sheet, or Reject to discard it."

    # ธนาคารยังยืนยันไม่เสร็จ = เรื่องชั่วคราวที่หายเองใน 1-2 นาที
    # ซ่อน Receive ไว้ เพราะกดรับตอนนี้คือรับโดยไม่มีใครตรวจกับธนาคารเลย
    still_pending = batch_result["error_type"] == "SLIP_PENDING"
    if not api_success:
        footer_message = (
            "The bank has not confirmed it yet. Press Check again in a minute — "
            "or Reject if the slip is wrong."
            if still_pending else
            "Verification is unavailable. Press Check again, or decide from the "
            "details above."
        )

    await send_manual_review_message(
        bot, chat_id, msg_id, batch_id, body_lines, footer_message,
        with_receive=not still_pending,
        with_retry=not api_success,
    )


SLIP_NOT_FOUND_TEXT = "This slip is no longer in the system. Please send it again."


def amount_is_unknown(txn) -> bool:
    """ไม่รู้ยอดเลยสักทาง ทั้งจากสลิปและจากข้อความในแชท"""
    return txn.api_total_amount is None and txn.chat_amount is None


def _amount_request_key(chat_id, msg_id) -> str:
    # message_id ซ้ำกันได้ข้ามกลุ่ม จึงต้องผูกกับ chat_id ด้วย
    return f"{chat_id}:{msg_id}"


def remember_amount_request(chat_id, question_msg_id, batch_id: str, user_id) -> None:
    """จำไว้ว่าข้อความคำถามใบไหน กำลังรอยอดของสลิปใบไหน จากใคร"""
    now = time.monotonic()
    for key, request in list(_amount_requests.items()):
        if request["expires_at"] <= now:
            _amount_requests.pop(key, None)

    _amount_requests[_amount_request_key(chat_id, question_msg_id)] = {
        "batch_id": batch_id,
        "user_id": str(user_id),
        "expires_at": now + AMOUNT_REQUEST_TTL_SECONDS,
    }


def find_amount_request(chat_id, question_msg_id) -> dict | None:
    request = _amount_requests.get(_amount_request_key(chat_id, question_msg_id))
    if request is None:
        return None
    if request["expires_at"] <= time.monotonic():
        _amount_requests.pop(_amount_request_key(chat_id, question_msg_id), None)
        return None
    return request


async def ask_for_amount(query, txn) -> None:
    """สลิปที่ไม่รู้ยอดเลย ให้แอดมินพิมพ์ยอดเองก่อน ค่อยไปต่อ

    ยังไม่จองสถานะตรงนี้ เพราะถ้าเขาไม่ตอบกลับมา สลิปจะค้างอยู่ในสถานะ Receive ตลอดกาล
    """
    await query.answer("This slip has no amount yet")
    question = await query.message.reply_text(
        "💬 Amount needed\n\n"
        f"{describe_user(query.from_user)}, this slip has no amount — "
        "the slip could not be read and the message did not say one.\n\n"
        "Reply to this message with the amount in THB (for example: 1500).",
    )
    remember_amount_request(
        query.message.chat_id, question.message_id, txn.batch_id, query.from_user.id
    )
    logger.info(
        "Asked for a manual amount | batch_id=%s | user=%s",
        txn.batch_id, describe_actor(query.from_user),
    )


async def handle_amount_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """รับข้อความที่แอดมินตอบกลับคำถามของบอท

    ตัวเดียวรับทั้งคำถามเรื่องยอดและเรื่องเวลาโอน เพราะทั้งคู่ใช้เงื่อนไขเดียวกัน
    (ตอบกลับด้วยข้อความ) — ถ้าแยกเป็นสอง handler ตัวหลังจะไม่มีวันได้ทำงาน
    """
    message = update.message
    if message is None or message.reply_to_message is None:
        return

    if await handle_qr_text_reply(message):
        return

    if await handle_time_reply(message):
        return

    request = find_amount_request(message.chat_id, message.reply_to_message.message_id)
    if request is None:
        return

    user = message.from_user
    if user is None or str(user.id) != request["user_id"]:
        # คนอื่นตอบแทนไม่ได้ แต่กด Receive เองเพื่อขอยอดของตัวเองได้
        await message.reply_text(
            "This amount was requested from someone else. "
            "Press Receive on the slip to enter it yourself."
        )
        return

    amount = parse_typed_amount(message.text)
    if amount is None:
        await message.reply_text(
            "⚠️ That is not a valid amount\n\n"
            "Reply again with a number greater than zero (for example: 1500)."
        )
        return

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == request["batch_id"]).first()
        if txn is None:
            _amount_requests.pop(_amount_request_key(message.chat_id, message.reply_to_message.message_id), None)
            await message.reply_text(SLIP_NOT_FOUND_TEXT)
            return

        if txn.status in DECIDED_STATUSES:
            _amount_requests.pop(_amount_request_key(message.chat_id, message.reply_to_message.message_id), None)
            await message.reply_text(
                f"This slip was already {'received' if txn.status == 'Receive' else 'rejected'}. "
                "The amount was not changed."
            )
            return

        txn.chat_amount = amount
        add_audit_log(db, txn.batch_id, "amount_entered_manually", actor=describe_actor(user))
        db.commit()
        batch_id = txn.batch_id
        time_still_unknown = transfer_time_is_unknown(txn)

    _amount_requests.pop(_amount_request_key(message.chat_id, message.reply_to_message.message_id), None)
    logger.info(
        "Manual amount accepted | batch_id=%s | amount=%s | user=%s",
        batch_id, format_amount(amount), describe_actor(user),
    )

    # สลิปที่ต้องพิมพ์ยอดเอง มักเป็นสลิปที่อ่านอะไรไม่ได้เลย เวลาโอนก็มักไม่รู้ด้วย
    # ถามต่อเลยตรงนี้ ดีกว่าปล่อยให้ไปกดเลือกธนาคารแล้วค่อยเด้งถาม จนต้องกดธนาคารซ้ำ
    if time_still_unknown:
        await message.reply_text(f"Amount set to {format_thb(amount)}")
        await ask_for_transfer_time(message, user, batch_id)
        return

    await message.reply_text(
        f"Amount set to {format_thb(amount)}\n\n"
        "Now choose the bank account this slip was paid into:",
        reply_markup=get_bank_selection_keyboard(batch_id),
    )


def describe_same_minute_duplicates(batch_id: str) -> str:
    """เตือนถ้ามีรายการอื่นยอดเท่ากันและโอนในนาทีเดียวกัน

    ใช้ได้ก็ต่อเมื่อรู้ทั้งยอดและเวลาโอนถึงระดับนาที ซึ่งเป็นข้อมูลที่เพิ่งกรอกเข้ามา
    เตือนอย่างเดียว ไม่บล็อก — สองคนโอนยอดเท่ากันในนาทีเดียวกันเกิดขึ้นได้จริง
    """
    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == batch_id).first()
        if txn is None or txn.transfer_at is None:
            return ""

        amount = txn.chat_amount if txn.chat_amount is not None else txn.api_total_amount
        if amount is None:
            return ""

        minute_start = txn.transfer_at.replace(second=0, microsecond=0)
        others = (
            db.query(Transaction)
            .filter(
                Transaction.batch_id != batch_id,
                Transaction.transfer_at >= minute_start,
                Transaction.transfer_at < minute_start + timedelta(minutes=1),
                Transaction.status.notin_(["Reject", "reject", NEEDS_QR_STATUS]),
            )
            .all()
        )
        matches = [
            t for t in others
            if amount in (t.chat_amount, t.api_total_amount)
        ]
        labels = [t.chat_trans_id or t.chat_user_id or "-" for t in matches]

    if not labels:
        return ""

    logger.info("Possible duplicate by amount and minute | batch_id=%s | others=%s",
                batch_id, ", ".join(labels))
    return (
        "⚠️ Possible duplicate — same amount at the same minute as: "
        + ", ".join(f"ID {label}" for label in labels)
        + ". Check before receiving.\n\n"
    )


def build_transfer_at(transfer_date, times_text: str):
    """ประกอบวันที่กับเวลาแรกสุดเป็นเวลาโอนจริง เก็บเป็น UTC แบบไม่มี tzinfo

    ยึดเวลาแรกสุดเป็นตัวแทนของชุด แบบเดียวกับตอนที่อ่านได้จากสลิปเอง
    """
    from services.gsheets import THAI_TZ

    hours = []
    for part in (times_text or "").split(","):
        piece = part.strip()
        if ":" in piece:
            hour, minute = piece.split(":", 1)
            hours.append((int(hour), int(minute)))
    if not hours:
        return None

    hour, minute = min(hours)
    local = datetime(transfer_date.year, transfer_date.month, transfer_date.day,
                     hour, minute, tzinfo=THAI_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def transfer_time_is_unknown(txn) -> bool:
    """อ่านเวลาโอนจากสลิปไม่ได้ (สลิปเสีย อ่าน QR ไม่ออก หรือ API ไม่ส่งเวลามา)"""
    return not (txn.transfer_time_text or "").strip()


def remember_time_request(chat_id, question_msg_id, batch_id: str, user_id) -> None:
    """จำไว้ว่าข้อความคำถามใบไหน กำลังรอเวลาโอนของสลิปใบไหน จากใคร"""
    now = time.monotonic()
    for key, request in list(_time_requests.items()):
        if request["expires_at"] <= now:
            _time_requests.pop(key, None)

    _time_requests[_amount_request_key(chat_id, question_msg_id)] = {
        "batch_id": batch_id,
        "user_id": str(user_id),
        "expires_at": now + AMOUNT_REQUEST_TTL_SECONDS,
    }


def find_time_request(chat_id, question_msg_id) -> dict | None:
    request = _time_requests.get(_amount_request_key(chat_id, question_msg_id))
    if request is None:
        return None
    if request["expires_at"] <= time.monotonic():
        _time_requests.pop(_amount_request_key(chat_id, question_msg_id), None)
        return None
    return request


async def ask_for_transfer_time(message, user, batch_id: str) -> None:
    """สลิปที่อ่านเวลาโอนไม่ได้ ให้แอดมินพิมพ์เวลาจากหน้าสลิปเอง

    รับ message ตรงๆ ไม่รับ query เพราะถูกเรียกได้ทั้งจากการกดปุ่ม
    และจากการตอบคำถามเรื่องยอด (ซึ่งไม่มี query ให้ใช้)

    ยังไม่จองสถานะเหมือนตอนถามยอด ถ้าเขาไม่ตอบ สลิปจะได้ไม่ค้างเป็น Receive ตลอดไป
    """
    question = await message.reply_text(
        "🕒 Transfer time needed\n\n"
        f"{describe_user(user)}, the time on this slip could not be read.\n\n"
        "Reply to this message with the date and time shown on the slip.\n"
        "For example: 8/8/69 0:18\n"
        "More than one slip in the picture? Add every time, separated by a comma "
        "(for example: 8/8/69 0:18, 0:37).",
    )
    remember_time_request(message.chat_id, question.message_id, batch_id, user.id)
    logger.info(
        "Asked for a manual transfer time | batch_id=%s | user=%s",
        batch_id, describe_actor(user),
    )


async def handle_time_reply(message) -> bool:
    """รับเวลาโอนที่แอดมินพิมพ์ตอบกลับมา คืน True ถ้าข้อความนี้เป็นคำตอบของคำถามเรื่องเวลา"""
    request = find_time_request(message.chat_id, message.reply_to_message.message_id)
    if request is None:
        return False

    key = _amount_request_key(message.chat_id, message.reply_to_message.message_id)
    user = message.from_user
    if user is None or str(user.id) != request["user_id"]:
        await message.reply_text(
            "This time was requested from someone else. "
            "Press Receive on the slip to enter it yourself."
        )
        return True

    transfer_date, times = parse_typed_date_and_times(message.text)
    # ต้องมีวันที่เสมอ ไม่ใช่แค่เวลา — ถ้าไม่มีวัน transfer_at จะเป็นค่าว่าง
    # แล้วด่านเตือนสลิปซ้ำระดับนาทีจะไม่ทำงานโดยไม่มีอะไรบอก
    if times is None or transfer_date is None:
        await message.reply_text(
            "⚠️ That is not a valid date and time\n\n"
            "Reply again like this: 8/8/69 0:18\n"
            "Several slips in one picture: 8/8/69 0:18, 0:37"
        )
        return True

    with SessionLocal() as db:
        txn = db.query(Transaction).filter(Transaction.batch_id == request["batch_id"]).first()
        if txn is None:
            _time_requests.pop(key, None)
            await message.reply_text(SLIP_NOT_FOUND_TEXT)
            return True

        if txn.status in DECIDED_STATUSES:
            _time_requests.pop(key, None)
            await message.reply_text(
                f"This slip was already {'received' if txn.status == 'Receive' else 'rejected'}. "
                "The time was not changed."
            )
            return True

        txn.transfer_time_text = clamp(times, TRANSFER_TIME_MAX)
        # มีวันที่แล้วจึงประกอบเป็น datetime เต็มได้ ใช้เวลาแรกสุดเป็นตัวแทนของชุด
        # ไม่มีวันที่ = ไม่เดา เพราะถ้าสลิปเป็นของเมื่อวานแล้วเดาเป็นวันนี้ การค้นจะผิด
        if transfer_date is not None:
            txn.transfer_at = build_transfer_at(transfer_date, times)
        add_audit_log(db, txn.batch_id, "transfer_time_entered_manually", actor=describe_actor(user))
        db.commit()
        batch_id = txn.batch_id

    _time_requests.pop(key, None)
    logger.info(
        "Manual transfer time accepted | batch_id=%s | time=%s | user=%s",
        batch_id, times, describe_actor(user),
    )

    warning = describe_same_minute_duplicates(batch_id)
    stamp = times if transfer_date is None else f"{transfer_date:%d/%m/%Y} {times}"
    await message.reply_text(
        f"Transfer time set to {stamp}\n\n"
        + warning +
        "Now choose the bank account this slip was paid into:",
        reply_markup=get_bank_selection_keyboard(batch_id),
    )
    return True


def describe_user(user) -> str:
    """ชื่อคนกดปุ่มสำหรับแสดงในข้อความ"""
    if user is None:
        return "unknown"
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = getattr(user, "full_name", None) or getattr(user, "first_name", None)
    return name or f"id {user.id}"


def describe_actor(user) -> str:
    """ตัวระบุคนกดสำหรับเก็บลง audit log — ใส่ id ไว้ด้วยเพราะชื่อกับ username เปลี่ยนได้"""
    if user is None:
        return "unknown"
    return f"{user.id} {describe_user(user)}"


def load_approver_ids() -> frozenset:
    """รายชื่อคนที่กดปุ่มได้ = เจ้าของจาก .env + คนที่ถูกเพิ่มไว้ใน DB

    เจ้าของใน .env เป็นกุญแจสำรอง ลบผ่านคำสั่งไม่ได้ กันกรณี DB ว่างหรือคนสุดท้ายเผลอลบตัวเอง
    """
    try:
        with SessionLocal() as db:
            stored_ids = get_approver_ids(db)
    except Exception:
        logger.exception("Could not read the approver list from the database")
        stored_ids = set()

    return frozenset(config.SLIP_APPROVER_IDS) | stored_ids


def is_owner(user) -> bool:
    """เจ้าของที่ตั้งไว้ใน .env — ถอดออกด้วยคำสั่งไม่ได้"""
    return user is not None and str(user.id) in config.SLIP_APPROVER_IDS


def is_approver(user) -> bool:
    """กดปุ่มได้เฉพาะคนที่อยู่ในรายชื่อเท่านั้น"""
    if user is None:
        return False
    return str(user.id) in load_approver_ids()


async def announce_already_decided(query, txn):
    """บอกว่ารายการนี้ถูกตัดสินไปแล้ว (กดซ้ำเอง หรือแอดมินอีกคนกดตัดหน้า)"""
    already_received = str(txn.status) == "Receive"
    await query.answer(
        f"This slip was already {'received' if already_received else 'rejected'}.",
        show_alert=True,
    )
    await query.edit_message_text(
        text=("✅ Already received" if already_received else "❌ Already rejected"),
        reply_markup=None,
    )


async def write_entry_to_sheet(txn):
    """เขียนลงชีทใน worker thread เพื่อไม่ให้บอททั้งตัวค้างระหว่างรอ Google

    ต้องคัดลอกค่าออกจาก ORM object ก่อนส่งข้ามเธรด (session ของ SQLAlchemy ไม่ thread-safe)
    """
    entry = SheetEntry.from_transaction(txn)
    return await asyncio.to_thread(append_to_sheet, entry)


async def save_receive_to_sheet(query, db, txn, bank_value: str, bot=None):
    """บันทึกรายการที่แอดมินกดรับลงชีท แล้วรายงานผลกลับไปที่ข้อความเดิม"""
    chat_id = txn.chat_id
    review_msg_id = txn.review_msg_id
    current_msg_id = getattr(getattr(query, "message", None), "message_id", None)

    if is_sheet_locked(db, txn.batch_id):
        await query.answer("This slip has already been saved.", show_alert=True)
        await query.edit_message_text(
            text=(f"✅ Already saved\n\n"
                  f"Account: {bank_value}\n"
                  f"Amount: {describe_txn_amount(txn)}"),
            reply_markup=None,
        )
        return

    actor = describe_actor(query.from_user)
    add_audit_log(db, txn.batch_id, "saving_started", actor=actor)
    await query.answer("Saving to today's sheet...")
    await query.edit_message_text(
        text=f"Saving to today's sheet...\n\nAccount: {bank_value}",
        reply_markup=None,
    )

    sheet_success, sheet_error_msg = await write_entry_to_sheet(txn)

    if sheet_success:
        add_audit_log(db, txn.batch_id, "sheet_saved", actor=actor)
        db.commit()
        await query.edit_message_text(
            text=(f"✅ Received\n\n"
                  f"ID: {txn.chat_trans_id or txn.chat_user_id or '-'}\n"
                  f"Account: {bank_value}\n"
                  f"Amount: {describe_txn_amount(txn)}\n"
                  f"Approved by: {describe_user(query.from_user)}\n\n"
                  f"Saved to today's sheet."),
            reply_markup=None,
        )
        # จบแล้ว ปุ่มชุดเดิมที่ยังค้างอยู่บนข้อความตรวจสอบต้องหายไปด้วย
        await close_review_buttons(bot, chat_id, review_msg_id, skip_msg_id=current_msg_id)
    else:
        # ปลดล็อกให้กดใหม่ได้ ไม่ค้างสถานะจนส่งสลิปซ้ำก็ไม่ได้
        unlock_after_failed_save(db, txn)
        await query.edit_message_text(
            text=("⚠️ Could not save to today's sheet\n\n"
                  f"{str(sheet_error_msg)[:200]}\n\n"
                  "Fix the sheet, then press Receive to try again."),
            reply_markup=get_approval_keyboard(txn.batch_id),
        )
        # ปุ่มย้ายมาอยู่ข้อความนี้แล้ว ปุ่มเก่าต้องปิด ไม่งั้นจะมีปุ่มลอยอยู่ 2 ชุด
        await close_review_buttons(bot, chat_id, review_msg_id, skip_msg_id=current_msg_id)
        txn.review_msg_id = str(current_msg_id) if current_msg_id is not None else review_msg_id
        db.commit()


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    callback_data = query.data or ""
    user = query.from_user

    # ยามอยู่ตรงนี้จุดเดียว จึงคุมครบทุกปุ่ม ทั้ง Receive/Reject, เลือกธนาคาร และ Back
    approver_ids = load_approver_ids()

    if not approver_ids:
        logger.error(
            "Button pressed but no approver is configured | user=%s | data=%s",
            describe_actor(user), callback_data,
        )
        await query.answer(
            "No one is allowed to approve slips yet. "
            "Ask the bot owner to set the approver list.",
            show_alert=True,
        )
        return

    if str(user.id) not in approver_ids:
        logger.warning(
            "Unauthorized button press | user=%s | data=%s",
            describe_actor(user), callback_data,
        )
        await query.answer(
            "You are not allowed to approve slips. "
            "Ask an approver to handle this one.",
            show_alert=True,
        )
        return

    logger.info("Button pressed | user=%s | data=%s", describe_actor(user), callback_data)

    with SessionLocal() as db:
        if callback_data.startswith("retry_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                return
            if is_sheet_locked(db, txn.batch_id):
                await query.answer("This slip is already saved to the sheet.", show_alert=True)
                return

            # ดึง QR ชุดเดิมออกมาตรวจซ้ำ คงรหัสชุดเดิมไว้ ไม่งั้นจะกลายเป็นสลิปคนละใบ
            known = [row.qr_ref for row in db.query(UsedQR)
                     .filter(UsedQR.batch_id == txn.batch_id).all()
                     if not row.qr_ref.startswith(PHOTO_KEY_PREFIX)]
            if not known:
                await query.answer("There is no QR to check. Send the QR first.",
                                   show_alert=True)
                return

            # จองสิทธิ์ตรวจก่อนเริ่ม กันคนกดรัวหรือหลายคนกดพร้อมกัน
            # ทุกครั้งที่กดคือโควตา EasySlip จริง และได้ข้อความรีวิวซ้อนกันหลายอัน
            # จนไม่รู้ว่าอันไหนคือของจริง
            if str(txn.status) in DECIDED_STATUSES:
                # ปุ่มเก่าบนจอคนอื่น ต้องบอกตามจริงว่าจบไปแล้วอย่างไร
                # ไม่ใช่เหมารวมว่า "มีคนกำลังตรวจอยู่"
                await announce_already_decided(query, txn)
                return
            if not claim_transaction(db, txn.batch_id, NEEDS_QR_STATUS):
                await query.answer("Someone is already checking this one.",
                                   show_alert=True)
                return
            add_audit_log(db, txn.batch_id, "reverified_by_hand", actor=describe_actor(user))
            db.commit()
            waiting = {"batch_id": txn.batch_id, "chat_id": txn.chat_id,
                       "msg_id": txn.msg_id, "caption": txn.raw_caption or "",
                       "photo_hash": txn.photo_hash}

            await query.answer("Checking with the bank again...")
            await query.edit_message_reply_markup(reply_markup=None)
            logger.info("Re-verifying on request | batch_id=%s | by=%s",
                        waiting["batch_id"], describe_actor(user))
            # ปล่อย connection คืน pool ก่อนงานยาว — ตรวจกับ EasySlip อาจกิน
            # เกิน 10 วินาที (มีการลองซ้ำ) แล้วยังต่อด้วยการเขียนชีท
            # ถ้าถือ connection ไว้ทั้งช่วงนั้น หลายคนกดพร้อมกันจะดูด pool จนหมด
            db.close()
            await process_slip_group(
                context.bot, waiting["chat_id"], waiting["msg_id"], waiting["caption"],
                known, photo_count=1, photo_hashes=[waiting["photo_hash"]],
                force_batch_id=waiting["batch_id"],
            )
            return
        if callback_data.startswith("addqr_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                return

            if is_sheet_locked(db, txn.batch_id):
                await query.answer(
                    "This slip is already saved to the sheet. Fix the sheet by hand instead.",
                    show_alert=True,
                )
                return

            # ใบที่ถูกปฏิเสธไปแล้วต้องปลดล็อกก่อน ไม่งั้นตรวจใหม่เสร็จก็บันทึกไม่ได้
            if str(txn.status) in DECIDED_STATUSES:
                add_audit_log(db, txn.batch_id, SHEET_REOPEN_ACTION, actor=describe_actor(user))
            txn.status = NEEDS_QR_STATUS
            add_audit_log(db, txn.batch_id, "extra_qr_requested", actor=describe_actor(user))
            db.commit()
            batch_id, chat_id_of_slip = txn.batch_id, txn.chat_id

            await query.answer("Send the QR of the slip that was missed")
            await query.edit_message_reply_markup(reply_markup=None)
            question = await query.message.reply_text(
                "➕ Add another QR\n\n"
                f"{describe_user(user)}, reply to THIS message with the QR of the slip "
                "that was missed — a picture of just the QR, or the code as text.\n\n"
                "Everything already read stays; the new one is added to it and the whole "
                "set is checked again.",
            )
            with SessionLocal() as fresh:
                waiting = fresh.query(Transaction).filter(
                    Transaction.batch_id == batch_id).first()
                if waiting is not None:
                    waiting.qr_request_msg_id = str(question.message_id)
                    fresh.commit()
            logger.info("Asked for an extra QR | batch_id=%s | by=%s",
                        batch_id, describe_actor(user))
            return
        if callback_data.startswith("dup_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                return
            if not claim_transaction(db, txn.batch_id, "Duplicate"):
                await announce_already_decided(query, txn)
                return

            add_audit_log(db, txn.batch_id, "marked_duplicate", actor=describe_actor(user))
            await query.answer("Marked as a duplicate")
            await query.edit_message_text(
                text=("♻️ Marked as duplicate\n\n"
                      f"Marked by: {describe_user(user)}\n\n"
                      "Nothing was saved to the sheet — the earlier slip already covers "
                      "this transfer. Use /status on the first one to see how it ended."),
                reply_markup=None,
            )
            await close_review_buttons(
                context.bot, txn.chat_id, txn.review_msg_id,
                skip_msg_id=getattr(getattr(query, "message", None), "message_id", None),
            )
            logger.info("Marked as duplicate | batch_id=%s | by=%s",
                        txn.batch_id, describe_actor(user))
            return

        if callback_data.startswith("noqr_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                return
            if str(txn.status).lower() != NEEDS_QR_STATUS:
                await query.answer("This slip is no longer waiting for a QR code.",
                                   show_alert=True)
                return

            waiting = {
                "batch_id": txn.batch_id,
                "chat_id": txn.chat_id,
                "msg_id": txn.msg_id,
                "caption": txn.raw_caption or "",
                "photo_hash": txn.photo_hash,
            }
            # ยกเว้นการตรวจกับธนาคารเป็นเรื่องใหญ่ ต้องรู้ว่าใครเป็นคนตัดสินใจ
            add_audit_log(db, txn.batch_id, "qr_declared_unreadable",
                          actor=describe_actor(user))
            db.commit()

            await query.answer("Recorded — this slip will be checked by hand")
            await query.edit_message_reply_markup(reply_markup=None)
            logger.info("QR declared unreadable | batch_id=%s | by=%s",
                        txn.batch_id, describe_actor(user))
            # ปล่อย connection คืน pool ก่อนงานยาว — ตรวจกับ EasySlip อาจกิน
            # เกิน 10 วินาที (มีการลองซ้ำ) แล้วยังต่อด้วยการเขียนชีท
            # ถ้าถือ connection ไว้ทั้งช่วงนั้น หลายคนกดพร้อมกันจะดูด pool จนหมด
            db.close()
            await process_slip_group(
                context.bot, waiting["chat_id"], waiting["msg_id"], waiting["caption"],
                [], photo_count=1, photo_hashes=[waiting["photo_hash"]],
                allow_without_qr=True,
            )
            return

        if callback_data.startswith("bank_"):
            _, short_ref, bank_value = callback_data.split("_", 2)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if not txn:
                await query.answer("Slip not found.", show_alert=True)
                await query.edit_message_text(text=SLIP_NOT_FOUND_TEXT, reply_markup=None)
                return

            # ถามเวลาโอนก่อนจองสถานะ — เส้นทางนี้มาจากการพิมพ์ยอดเอง ซึ่งข้ามด่านเวลาไป
            # ต้องถามตรงนี้ด้วย ไม่งั้นสลิปที่อ่านไม่ออกจะลงชีทโดยไม่มีเวลาโอน
            if transfer_time_is_unknown(txn):
                await query.answer("This slip has no transfer time yet")
                await ask_for_transfer_time(query.message, query.from_user, txn.batch_id)
                return

            # ปุ่มเลือกธนาคารอาจค้างอยู่บนจอของแอดมินอีกคน แม้รายการจะถูกตัดสินไปแล้ว
            if not claim_transaction(db, txn.batch_id, "Receive"):
                await announce_already_decided(query, txn)
                return

            txn.chat_bank = bank_value
            txn.receiver_account = bank_value
            add_audit_log(db, txn.batch_id, "manual_bank_selected", actor=describe_actor(user))
            await save_receive_to_sheet(query, db, txn, bank_value, bot=context.bot)
            return

        if callback_data.startswith("back_"):
            _, short_ref = callback_data.split("_", 1)
            txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()
            if txn:
                await query.answer("Back to Receive / Reject")
                await query.edit_message_reply_markup(reply_markup=get_approval_keyboard(txn.batch_id))
            return

        action, short_ref = callback_data.split('_', 1)
        txn = db.query(Transaction).filter(Transaction.batch_id.startswith(short_ref)).first()

        if not txn:
            await query.answer("Slip not found.", show_alert=True)
            await query.edit_message_text(text=SLIP_NOT_FOUND_TEXT, reply_markup=None)
            return

        if txn.status in DECIDED_STATUSES:
            await announce_already_decided(query, txn)
            return

        # ใบที่กำลังรอ QR ยังยืนยันกับธนาคารไม่ได้ ห้ามรับเด็ดขาด
        # ปุ่มเก่าบนข้อความเดิมยังกดได้อยู่ (เช่นหลังมีคนกด Add another QR)
        # ถ้าไม่กันตรงนี้ จะรับเข้าชีทโดยข้ามด่านที่เพิ่งตั้งไว้ทั้งหมด
        if str(txn.status).lower() == NEEDS_QR_STATUS and action != "reject":
            await query.answer(
                "This slip is waiting for its QR code. Send the QR first, "
                "or press Reject to discard it.",
                show_alert=True,
            )
            return

        if action == "reject":
            if not claim_transaction(db, txn.batch_id, "Reject"):
                await announce_already_decided(query, txn)
                return

            add_audit_log(db, txn.batch_id, "admin_rejected", actor=describe_actor(user))
            await query.answer("Slip rejected")
            await query.edit_message_text(
                text=("❌ Rejected\n\n"
                      f"Rejected by: {describe_user(user)}\n\n"
                      "Nothing was saved to the sheet. "
                      "Send the corrected slip again if needed."),
                reply_markup=None,
            )
            await close_review_buttons(
                context.bot, txn.chat_id, txn.review_msg_id,
                skip_msg_id=getattr(getattr(query, "message", None), "message_id", None),
            )
            return

        # ไม่รู้ยอดเลย บันทึกไปก็ได้แถวที่ช่องจำนวนเงินว่าง ต้องให้พิมพ์ยอดมาก่อน
        if amount_is_unknown(txn):
            await ask_for_amount(query, txn)
            return

        # อ่านเวลาโอนจากสลิปไม่ได้ ต้องให้แอดมินอ่านจากหน้าสลิปมาให้
        if transfer_time_is_unknown(txn):
            await query.answer("This slip has no transfer time yet")
            await ask_for_transfer_time(query.message, query.from_user, txn.batch_id)
            return

        # Receive: ใช้บัญชีที่อ่านได้จากสลิป ถ้าไม่รู้ค่อยให้แอดมินเลือกเอง
        known_bank = resolve_known_bank_value(txn)

        if action == "agent" or not known_bank:
            # ยังไม่จองสิทธิ์ตรงนี้ เพราะยังไม่ได้ตัดสินอะไร แค่ขอให้เลือกธนาคารก่อน
            # สลับแค่ปุ่ม ไม่ทับข้อความผลตรวจ กด Back แล้วจะได้ข้อความเดิมครบ
            await query.answer("Choose the bank account for this slip")
            await query.edit_message_reply_markup(
                reply_markup=get_bank_selection_keyboard(txn.batch_id)
            )
            return

        if not claim_transaction(db, txn.batch_id, "Receive"):
            await announce_already_decided(query, txn)
            return

        txn.chat_bank = known_bank
        add_audit_log(db, txn.batch_id, "bank_taken_from_slip", actor=describe_actor(user))
        await save_receive_to_sheet(query, db, txn, known_bank, bot=context.bot)
