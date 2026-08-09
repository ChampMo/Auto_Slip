import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers import (
    STATUS_ICONS,
    build_checks_from_record,
    describe_actor,
    describe_user,
    format_check_results,
    format_thb,
    load_approver_ids,
    message_link,
    remember_review_message,
)
from bot.keyboards import get_approval_keyboard
from core.captions import extract_data_from_caption
from core.config import config
from core.version import describe_version
from core.matcher import GROUP_CATEGORY, REJECTED_STATUSES
from database.crud import (
    add_approver,
    add_audit_log,
    get_audit_trail,
    list_approvers,
    remove_approver,
    SHEET_REOPEN_ACTION,
)
from database.models import Transaction
from database.session import SessionLocal
from services.gdrive import drive_service
from services.gsheets import (
    BUSINESS_DAY_STARTS_AT,
    BUSINESS_TZ,
    get_business_now,
    get_month_file_name,
    get_sheet_name_for_datetime,
    get_year_folder_name,
    sheets_service,
    to_bangkok_clock,
)

logger = logging.getLogger(__name__)

# แปลงชื่อ action ใน audit log ให้คนอ่านรู้เรื่อง
ACTION_LABELS = {
    "api_verified_matched": "Passed all checks",
    "manual_review_required": "Sent for manual review",
    "manual_review_no_qr": "No QR code — sent for manual review",
    "auto_rejected_mismatch": "Rejected automatically",
    "admin_rejected": "Rejected",
    "bank_taken_from_slip": "Account taken from the slip",
    "manual_bank_selected": "Account chosen manually",
    "saving_started": "Saving to the sheet",
    "sheet_saved": "Saved to the sheet",
    "batch_reopened": "Reopened for another try",
    "duplicate_batch_resent": "Sent again (duplicate)",
    "duplicate_qr_found": "A slip in this batch was already used",
    "sent_back_for_checking": "Sent back for checking",
    "amount_entered_manually": "Amount typed in by hand",
    "transfer_time_entered_manually": "Transfer time typed in by hand",
    "qr_requested": "Asked the sender for the QR code",
    "qr_supplied_by_hand": "QR code sent in by hand",
    "qr_declared_unreadable": "QR reported unreadable — checked by hand",
    "duplicate_photo_found": "The same picture was already sent",
    "marked_duplicate": "Marked as a duplicate by hand",
    "reverified_by_hand": "Checked with the bank again on request",
    "extra_qr_requested": "Someone reported a missed slip — asked for its QR",
}

# สถานะที่ยังไม่จบ ต้องมีคนมาจัดการ
# needs_qr = รอ QR จากคนส่ง ยังตัดสินไม่ได้ แต่ต้องเห็นใน /pending ไม่งั้นจะถูกลืม
OPEN_STATUSES = ("pending", "interrupted", "needs_qr")


def to_bangkok(naive_utc: datetime) -> datetime:
    """created_at / timestamp เก็บเป็น UTC แบบไม่มี tzinfo — แปลงเป็นนาฬิกาไทยก่อนแสดง

    ใช้แสดงผลเท่านั้น การตัดสินว่าเป็นของวันไหนต้องใช้ business_day_range_utc
    """
    return to_bangkok_clock(naive_utc.replace(tzinfo=timezone.utc))


def business_day_range_utc(moment: datetime = None) -> tuple:
    """ขอบเขตของ 'วันนี้' ตามปฏิทินธุรกิจ แปลงกลับเป็น UTC เพื่อเทียบกับ created_at

    วันธุรกิจเริ่ม 23:00 ตามเวลาไทย ดังนั้นสลิปที่เข้ามา 23:30 คืนนี้
    จะถูกนับเป็นของ "พรุ่งนี้" เหมือนกับที่มันถูกบันทึกลงแท็บของพรุ่งนี้
    """
    now = moment or get_business_now()
    start = now.astimezone(BUSINESS_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None),
        end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def describe_age(naive_utc: datetime) -> str:
    """ผ่านมานานแค่ไหนแล้ว"""
    minutes = int((datetime.utcnow() - naive_utc).total_seconds() // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    return f"{hours // 24} day(s) ago"


def can_read_status(user, chat_id) -> bool:
    """ในกลุ่มที่ลงทะเบียนไว้ ทุกคนดูสถานะได้ (ข้อมูลอยู่ในกลุ่มนั้นอยู่แล้ว)
    นอกกลุ่ม เช่นทักมาหาบอทตรงๆ ให้เฉพาะผู้อนุมัติ
    """
    if str(chat_id) in GROUP_CATEGORY:
        return True
    return user is not None and str(user.id) in load_approver_ids()

NOT_ALLOWED_TEXT = (
    "You are not allowed to manage the approver list. "
    "Ask someone who already has access."
)

USAGE_TEXT = (
    "Reply to a message from the person and send the command again, "
    "or pass their numeric Telegram ID.\n\n"
    "A @username cannot be used — Telegram does not let bots look up an ID from a username. "
    "The person can send /myid to get theirs."
)


def resolve_target(message, args) -> tuple:
    """หาว่าคำสั่งพูดถึงใคร — จาก reply เป็นหลัก ถ้าไม่มีก็รับเป็นตัวเลข id

    คืน (user_id, username, display_name, error) โดย error เป็นข้อความบอกเหตุถ้าหาไม่ได้
    """
    replied = getattr(message, "reply_to_message", None)
    if replied is not None and getattr(replied, "from_user", None) is not None:
        target = replied.from_user
        if getattr(target, "is_bot", False):
            return None, None, None, "That is a bot — bots cannot approve slips."
        return (
            str(target.id),
            getattr(target, "username", None),
            getattr(target, "full_name", None),
            None,
        )

    if args:
        raw = str(args[0]).strip().lstrip("@")
        if raw.isdigit():
            return raw, None, None, None
        return None, None, None, USAGE_TEXT

    return None, None, None, USAGE_TEXT


async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """บอก user id ของตัวเอง — ใช้ตอนกรอกรายชื่อเจ้าของใน .env"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    await message.reply_text(
        f"Your Telegram ID: {user.id}\n"
        f"Name: {describe_user(user)}"
    )


async def describe_by_id(bot, user_id: str) -> str:
    """หาชื่อจากเลข id เพื่อแสดงผล

    ไฟล์ตั้งค่าเก็บแต่ตัวเลข จึงต้องถาม Telegram เอาชื่อมา
    ถามไม่ได้ก็ไม่เป็นไร แสดงเลข id ไปตามเดิม — ดีกว่าให้ทั้งคำสั่งล้มเพราะเรื่องแสดงผล
    """
    try:
        chat = await bot.get_chat(int(user_id))
    except Exception as exc:
        logger.info("Could not look up the name for id %s: %s", user_id, exc)
        return f"id {user_id}"

    username = getattr(chat, "username", None)
    name = getattr(chat, "full_name", None) or getattr(chat, "first_name", None)
    label = f"@{username}" if username else (name or f"id {user_id}")
    return label if label == f"id {user_id}" else f"{label} (id {user_id})"


async def approvers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ดูรายชื่อคนที่กดอนุมัติได้"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text(NOT_ALLOWED_TEXT)
        return

    with SessionLocal() as db:
        stored = list_approvers(db)
        lines = [
            f"• {approver.username and '@' + approver.username or approver.display_name or 'id ' + approver.user_id}"
            f" (id {approver.user_id}) — added by {approver.added_by or 'unknown'}"
            for approver in stored
        ]

    owner_ids = sorted(config.SLIP_APPROVER_IDS)
    owner_lines = [
        f"• {await describe_by_id(context.bot, owner_id)}"
        + (" — you" if str(user.id) == owner_id else "")
        for owner_id in owner_ids
    ]

    body = "\n".join(lines) if lines else "• (nobody added yet)"
    owner_body = "\n".join(owner_lines) if owner_lines else "• (none configured)"

    await message.reply_text(
        f"🔐 Owners from config: {len(owner_ids)}\n"
        "They can always approve and cannot be removed with a command.\n\n"
        f"{owner_body}\n\n"
        f"Added here with /approver_add: {len(lines)}\n\n"
        f"{body}"
    )


async def approver_add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """เพิ่มคนเข้ารายชื่อ (reply ไปที่ข้อความของเขา หรือใส่ id)"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text(NOT_ALLOWED_TEXT)
        return

    user_id, username, display_name, error = resolve_target(message, context.args)
    if error:
        await message.reply_text(error)
        return

    if user_id in config.SLIP_APPROVER_IDS:
        await message.reply_text("That person is already an owner in the config.")
        return

    with SessionLocal() as db:
        added = add_approver(db, user_id, username, display_name, describe_actor(user))
        db.commit()

    who = f"@{username}" if username else (display_name or f"id {user_id}")
    if not added:
        await message.reply_text(f"{who} can already approve slips.")
        return

    logger.info("Approver added | target=%s | by=%s", user_id, describe_actor(user))
    await message.reply_text(
        f"✅ Added {who} (id {user_id})\n\n"
        f"Added by {describe_user(user)}. They can now press Receive and Reject."
    )


async def approver_remove_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ถอดคนออกจากรายชื่อ (reply ไปที่ข้อความของเขา หรือใส่ id)"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text(NOT_ALLOWED_TEXT)
        return

    user_id, username, display_name, error = resolve_target(message, context.args)
    if error:
        await message.reply_text(error)
        return

    if user_id in config.SLIP_APPROVER_IDS:
        await message.reply_text(
            "That person is an owner in the config and cannot be removed with a command. "
            "Edit SLIP_APPROVER_IDS in .env and restart the bot instead."
        )
        return

    with SessionLocal() as db:
        stored = list_approvers(db)
        # กันล็อกเอาต์: ถ้าไม่มีเจ้าของใน .env เลย ห้ามถอดคนสุดท้ายออก
        if not config.SLIP_APPROVER_IDS and len(stored) <= 1:
            await message.reply_text(
                "This is the last approver and there is no owner configured in .env — "
                "removing them would lock everyone out. Add someone else first."
            )
            return

        removed = remove_approver(db, user_id, describe_actor(user))
        db.commit()

    who = f"@{username}" if username else (display_name or f"id {user_id}")
    if not removed:
        await message.reply_text(f"{who} is not in the approver list.")
        return

    logger.info("Approver removed | target=%s | by=%s", user_id, describe_actor(user))
    await message.reply_text(f"🚫 Removed {who} (id {user_id}) — they can no longer approve slips.")


def _find_by_caption(query, caption: str):
    """ค้นรายการจากรหัสที่เขียนอยู่ใน caption ของข้อความที่ reply มา"""
    data = extract_data_from_caption(caption or "")
    wanted = data["trans_id"] or data["user_id"]
    if not wanted:
        return None
    return (
        query.filter(
            (Transaction.chat_trans_id == wanted) | (Transaction.chat_user_id == wanted)
        )
        .order_by(Transaction.created_at.desc())
        .first()
    )


def find_slip(db, chat_id, replied, args):
    """หาสลิปจาก reply หรือจาก ID ที่พิมพ์มา — คืน (txn, ข้อความบอกเหตุถ้าหาไม่เจอ)"""
    query = db.query(Transaction)

    if replied is not None:
        txn = query.filter(
            Transaction.chat_id == str(chat_id),
            Transaction.msg_id == str(replied.message_id),
        ).first()
        if txn is not None:
            return txn, None

        # ข้อความนั้นไม่มีรายการผูกอยู่ — เกิดกับสลิปที่ส่งซ้ำ เพราะรายการยังผูกกับใบแรก
        # ลองอ่านรหัสจาก caption ของข้อความนั้นแล้วค้นให้แทน จะได้ไม่ต้องไปไล่หาใบแรกเอง
        found = _find_by_caption(query, getattr(replied, "caption", None))
        if found is not None:
            return found, None

        return None, (
            "No slip is linked to that message.\n\n"
            "Reply to the photo that carries the caption — for an album only that "
            "one message is linked. You can also pass the ID instead."
        )

    # -b บอกว่าให้ค้นด้วย batch_id ตรงๆ ไม่ต้องไปไล่ทั้งช่อง Trans ID และ User
    # รับแบบย่อได้ด้วย เพราะ batch_id ยาว 32 ตัว พิมพ์ครบทุกตัวลำบาก
    if args and str(args[0]).strip().lower() in ("-b", "--batch"):
        wanted = str(args[1]).strip() if len(args) > 1 else ""
        if not wanted:
            return None, "Pass the batch id too, like /status -b 4f3c9a2b"
        matches = (
            query.filter(Transaction.batch_id.startswith(wanted))
            .order_by(Transaction.created_at.desc())
            .limit(2)
            .all()
        )
        if not matches:
            return None, f"No slip found for batch {wanted}."
        if len(matches) > 1:
            return None, (
                f"More than one slip starts with {wanted}. "
                "Use more characters of the batch id."
            )
        return matches[0], None

    if args:
        wanted = str(args[0]).strip()
        txn = (
            query.filter(
                (Transaction.chat_trans_id == wanted) | (Transaction.chat_user_id == wanted)
            )
            .order_by(Transaction.created_at.desc())
            .first()
        )
        if txn is None:
            return None, f"No slip found for ID {wanted}."
        return txn, None

    return None, None


def slip_header(txn) -> list:
    """บรรทัดหัวที่ใช้ร่วมกันทั้ง /status และ /recheck

    เวลาโอนคือเวลาที่เงินออกจากบัญชีผู้โอน ไม่ใช่เวลาที่บอทได้รับรูป
    สลิปเก่าที่บันทึกไว้ก่อนระบบจะเก็บเวลา จะไม่มีบรรทัดนี้ ซึ่งไม่ใช่ความผิดพลาด
    """
    lines = [f"ID: {txn.chat_trans_id or txn.chat_user_id or '-'}"]
    if (txn.transfer_time_text or "").strip():
        lines.append(f"Transferred at: {txn.transfer_time_text.strip()}")
    lines.append("")
    return lines


async def recheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """เอาสลิปที่ถูกปฏิเสธไปแล้ว กลับมาให้คนตัดสินใหม่

    ใช้ตอนระบบปฏิเสธผิด เช่น อ่านยอดในข้อความไม่ออก
    ไม่ได้ยิงตรวจกับธนาคารซ้ำ แค่เอาปุ่มกลับมาให้กด
    ไม่เปิดใบที่บันทึกลงชีทไปแล้ว เพราะแถวถูกเขียนไปแล้ว การเปิดใหม่จะทำให้ซ้ำ
    """
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text("Only approvers can send a slip back for checking.")
        return

    replied = getattr(message, "reply_to_message", None)
    args = context.args or []

    with SessionLocal() as db:
        txn, problem = find_slip(db, message.chat_id, replied, args)
        if problem:
            await message.reply_text(problem)
            return
        if txn is None:
            await message.reply_text(
                "Reply to the slip photo and send /recheck, or pass the ID: /recheck 0000123"
            )
            return

        status = str(txn.status)
        if status == "Receive":
            await message.reply_text(
                "❌ This slip was already saved to the sheet\n\n"
                "Sending it back would write a second row for the same money. "
                "Fix the row in the sheet by hand instead."
            )
            return

        if status.lower() not in REJECTED_STATUSES:
            await message.reply_text(
                f"This slip is not finished yet ({STATUS_ICONS.get(status.lower(), status)}). "
                "Nothing to send back — the buttons are still live on the original message."
            )
            return

        batch_id = txn.batch_id
        txn.status = "pending"
        actor = describe_actor(user)
        add_audit_log(db, batch_id, "sent_back_for_checking", actor=actor)
        # ปลดล็อกการเขียนชีทของรอบก่อน ไม่งั้นกด Receive แล้วจะขึ้นว่าบันทึกไปแล้ว
        add_audit_log(db, batch_id, SHEET_REOPEN_ACTION, actor=actor)
        db.commit()

        # แสดงผลเทียบแบบเดียวกับตอนที่ระบบตรวจครั้งแรก จะได้ตัดสินจากข้อมูลชุดเดียวกัน
        details = slip_header(txn)
        if txn.caption_warning:
            details.extend([f"⚠️ {txn.caption_warning}", ""])
        details.extend(format_check_results(build_checks_from_record(txn)))

    logger.info("Slip sent back for checking | batch_id=%s | by=%s", batch_id, actor)

    sent = await message.reply_text(
        "♻️ Sent back for checking\n\n"
        f"Sent back by: {describe_user(user)}\n\n"
        + "\n".join(details)
        + "\n\nThese are the details already on file — the slip was not verified with the bank again.\n"
        "Choose Receive to save it to today's sheet, or Reject to discard it.",
        reply_markup=get_approval_keyboard(batch_id),
    )
    remember_review_message(batch_id, getattr(sent, "message_id", None))


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ดูว่าสลิปใบนั้นจบยังไง — reply ไปที่รูปสลิป หรือใส่ Trans ID"""
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return

    chat_id = message.chat_id
    if not can_read_status(user, chat_id):
        await message.reply_text("You are not allowed to look up slips here.")
        return

    replied = getattr(message, "reply_to_message", None)
    args = context.args or []

    with SessionLocal() as db:
        # ใช้ตัวค้นหาตัวเดียวกับ /recheck จะได้ทำงานเหมือนกันเสมอ
        txn, problem = find_slip(db, chat_id, replied, args)
        if problem:
            await message.reply_text(problem)
            return
        if txn is None:
            await message.reply_text(
                "Reply to the slip photo and send /status again, "
                "or use /status <ID> with the ID written in the caption."
            )
            return

        headline = STATUS_ICONS.get(str(txn.status).lower(), f"Status: {txn.status}")

        # กางผลตรวจแบบเดียวกับตอนที่บอทถามในกลุ่ม จะได้ไม่ต้องเดาว่าเดิมติดตรงไหน
        details = slip_header(txn)
        if txn.caption_warning:
            details.extend([f"⚠️ {txn.caption_warning}", ""])
        details.extend(format_check_results(build_checks_from_record(txn)))
        if txn.api_total_amount is None and not txn.sender_names:
            # ไม่มีข้อมูลจากสลิปเลย ผลตรวจข้างบนจึงติดทุกข้อโดยปริยาย ต้องบอกสาเหตุ
            details += ["", "The slip itself was never read — nothing came back to compare against."]

        trail = []
        for entry in get_audit_trail(db, txn.batch_id):
            label = ACTION_LABELS.get(entry.action, entry.action)
            when = to_bangkok(entry.timestamp).strftime("%d-%m %H:%M")
            actor = f" — {entry.actor.split(' ', 1)[-1]}" if entry.actor else ""
            trail.append(f"  {when}  {label}{actor}")

        # คัดออกมาก่อนปิด session จะได้ใช้ต่อได้โดยไม่ต้องถือ connection ไว้
        slip_chat_id, slip_msg_id, slip_batch_id = txn.chat_id, txn.msg_id, txn.batch_id

    body = [headline, ""] + details
    if trail:
        body += ["", "History:"] + trail

    # ลิงก์กลับไปข้อความต้นทาง หาสลิปใบนั้นในกลุ่มที่คุยกันเยอะได้ทันที
    link = message_link(slip_chat_id, slip_msg_id)
    if link:
        body += ["", f"Original message: {link}"]
    # โชว์ batch id ไว้ให้เอาไปใช้กับ /status -b ได้ ถ้าข้อความเดิมหาไม่เจอแล้ว
    body += [f"Batch: {slip_batch_id[:12]}"]

    await message.reply_text("\n".join(body))


async def pending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """สลิปที่ยังรอคนกดอยู่ในกลุ่มนี้"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text("Only approvers can list waiting slips.")
        return

    chat_id = str(message.chat_id)
    with SessionLocal() as db:
        waiting = (
            db.query(Transaction)
            .filter(Transaction.chat_id == chat_id, Transaction.status.in_(OPEN_STATUSES))
            .order_by(Transaction.created_at.desc())
            .limit(10)
            .all()
        )
        rows = [
            {
                "id": txn.chat_trans_id or txn.chat_user_id or "no ID",
                "amount": txn.chat_amount,
                "age": describe_age(txn.created_at),
                "link": message_link(txn.chat_id, txn.msg_id),
                "interrupted": str(txn.status).lower() == "interrupted",
            }
            for txn in waiting
        ]
        total_open = (
            db.query(Transaction)
            .filter(Transaction.chat_id == chat_id, Transaction.status.in_(OPEN_STATUSES))
            .count()
        )

    if not rows:
        await message.reply_text("✅ Nothing is waiting — every slip in this chat has been handled.")
        return

    lines = [f"⏳ {total_open} slip(s) waiting", ""]
    for row in rows:
        mark = " (interrupted — must be sent again)" if row["interrupted"] else ""
        lines.append(f"• {row['id']} · {format_thb(row['amount'])} · {row['age']}{mark}")
        if row["link"]:
            lines.append(f"  {row['link']}")

    if total_open > len(rows):
        lines.append(f"\n...and {total_open - len(rows)} more.")

    await message.reply_text("\n".join(lines))


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """สรุปของวันนี้ตามเวลากรุงเทพ"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text("Only approvers can see the daily summary.")
        return

    now = get_business_now()
    start_utc, end_utc = business_day_range_utc(now)
    chat_id = str(message.chat_id)
    in_group = chat_id in GROUP_CATEGORY

    with SessionLocal() as db:
        query = db.query(Transaction).filter(
            Transaction.created_at >= start_utc, Transaction.created_at < end_utc
        )
        if in_group:
            query = query.filter(Transaction.chat_id == chat_id)
        slips = query.all()

    received = [t for t in slips if str(t.status).lower() == "receive"]
    rejected = [t for t in slips if str(t.status).lower() == "reject"]
    waiting = [t for t in slips if str(t.status).lower() in OPEN_STATUSES]
    total = sum(
        (t.api_total_amount if t.api_total_amount is not None else t.chat_amount) or 0.0
        for t in received
    )

    scope = "this chat" if in_group else "all chats"
    await message.reply_text(
        f"📊 Today · {get_sheet_name_for_datetime(now)} · {scope}\n\n"
        f"Received  {len(received)} · {format_thb(total)}\n"
        f"Rejected  {len(rejected)}\n"
        f"Waiting   {len(waiting)}\n\n"
        f"Sheet: {get_year_folder_name(now)}/{get_month_file_name(now)} "
        f"→ tab {get_sheet_name_for_datetime(now)}\n\n"
        f"A business day runs from {BUSINESS_DAY_STARTS_AT} to {BUSINESS_DAY_STARTS_AT} Bangkok time."
    )


def collect_sheet_health() -> dict:
    """เช็คว่าเข้าถึง Drive/ชีทได้จริงไหม — ทำงานช้าเพราะยิง API จึงต้องรันใน worker thread"""
    now = get_business_now()
    next_month = now + timedelta(days=32)
    result = {}

    folder_name = get_year_folder_name(now)
    try:
        folder_id = drive_service.get_folder_id_by_name(folder_name)
        result["folder"] = f"OK — {folder_name}" if folder_id else f"MISSING — {folder_name}"
    except Exception as exc:
        result["folder"] = f"ERROR — {exc}"
        folder_id = None

    file_name = get_month_file_name(now)
    try:
        file_id = drive_service.get_spreadsheet_id_by_name(file_name, folder_id) if folder_id else None
        result["file"] = f"OK — {file_name}" if file_id else f"MISSING — {file_name}"
    except Exception as exc:
        result["file"] = f"ERROR — {exc}"

    tab_name = get_sheet_name_for_datetime(now)
    try:
        spreadsheet = sheets_service._get_dynamic_spreadsheet(now)
        spreadsheet.worksheet(tab_name)
        result["tab"] = f"OK — {tab_name}"
    except Exception as exc:
        result["tab"] = f"MISSING — {tab_name} ({str(exc)[:60]})"

    next_folder = get_year_folder_name(next_month)
    next_file = get_month_file_name(next_month)
    try:
        next_folder_id = drive_service.get_folder_id_by_name(next_folder)
        next_file_id = (
            drive_service.get_spreadsheet_id_by_name(next_file, next_folder_id)
            if next_folder_id else None
        )
        result["next"] = f"OK — {next_file}" if next_file_id else f"not created yet — {next_folder}/{next_file}"
    except Exception as exc:
        result["next"] = f"ERROR — {exc}"

    return result


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ตรวจว่าระบบยังต่อ Drive/ชีทได้ และงานตามเวลาถูกตั้งไว้ครบ"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return

    if str(user.id) not in load_approver_ids():
        await message.reply_text("Only approvers can run the health check.")
        return

    notice = await message.reply_text("🩺 Checking...")

    try:
        with SessionLocal() as db:
            slip_count = db.query(Transaction).count()
        database_line = f"OK — {slip_count} slips recorded"
    except Exception as exc:
        database_line = f"ERROR — {exc}"

    sheet = await asyncio.to_thread(collect_sheet_health)

    approver_ids = load_approver_ids()
    owners = len(config.SLIP_APPROVER_IDS)

    job_lines = []
    scheduler = context.application.bot_data.get("scheduler")
    if scheduler is not None:
        for job in scheduler.get_jobs():
            # แสดงเป็นนาฬิกาไทย ไม่ใช่เวลาธุรกิจ คนอ่านจะได้เทียบกับนาฬิกาจริงได้
            when = to_bangkok_clock(job.next_run_time).strftime("%d-%m %H:%M") if job.next_run_time else "not scheduled"
            job_lines.append(f"  {job.name or job.id}: {when}")
    if not job_lines:
        job_lines = ["  (scheduler is not running)"]

    await notice.edit_text(
        f"🩺 Health · {describe_version()}\n\n"
        f"Database:     {database_line}\n"
        f"Drive folder: {sheet['folder']}\n"
        f"Month file:   {sheet['file']}\n"
        f"Today's tab:  {sheet['tab']}\n"
        f"Next month:   {sheet['next']}\n"
        f"Approvers:    {len(approver_ids)} ({owners} owner(s) from config)\n\n"
        "Scheduled jobs:\n" + "\n".join(job_lines)
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """รายการคำสั่งทั้งหมด"""
    message = update.effective_message
    if message is None:
        return

    await message.reply_text(
        "🤖 Auto Slip — commands\n\n"
        "Everyone:\n"
        "  /status — reply to a slip photo to see how it ended,\n"
        "            or /status <ID>, or /status -b <batch>\n"
        "  /myid — show your Telegram ID\n"
        "  /help — this list\n\n"
        "Approvers only:\n"
        "  /pending — slips still waiting for a decision\n"
        "  /recheck — reply to a rejected slip to decide it again\n"
        "  /today — today's totals\n"
        "  /health — check Drive, the sheet and scheduled jobs\n"
        "  /approvers — who can approve slips\n"
        "  /approver_add — reply to someone to let them approve\n"
        "  /approver_remove — reply to someone to take it away\n\n"
        "Send a slip photo to this chat and the bot checks it automatically.\n\n"
        f"Version {describe_version()}"
    )
