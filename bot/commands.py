import asyncio
import logging
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ContextTypes

from bot.handlers import describe_actor, describe_user, format_thb, load_approver_ids
from core.config import config
from core.matcher import GROUP_CATEGORY
from database.crud import (
    add_approver,
    get_audit_trail,
    list_approvers,
    remove_approver,
)
from database.models import Transaction
from database.session import SessionLocal
from services.gdrive import drive_service
from services.gsheets import (
    BANGKOK_TZ,
    get_bangkok_now,
    get_month_file_name,
    get_sheet_name_for_datetime,
    get_year_folder_name,
    sheets_service,
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
}

STATUS_ICONS = {
    "receive": "✅ Received",
    "reject": "❌ Rejected",
    "pending": "⏳ Waiting for a decision",
    "interrupted": "⚠️ Interrupted while saving — please send it again",
}

# สถานะที่ยังไม่จบ ต้องมีคนมาจัดการ
OPEN_STATUSES = ("pending", "interrupted")


def to_bangkok(naive_utc: datetime) -> datetime:
    """created_at / timestamp เก็บเป็น UTC แบบไม่มี tzinfo — แปลงเป็นเวลากรุงเทพก่อนแสดง"""
    return naive_utc.replace(tzinfo=timezone.utc).astimezone(BANGKOK_TZ)


def bangkok_day_range_utc(moment: datetime = None) -> tuple:
    """ขอบเขตของ 'วันนี้' ตามเวลากรุงเทพ แปลงกลับเป็น UTC เพื่อเทียบกับ created_at

    ถ้าเทียบวันตรงๆ กับ UTC สลิปช่วงเที่ยงคืนถึง 7 โมงเช้าจะถูกนับเป็นวันก่อนหน้า
    """
    now_bkk = moment or get_bangkok_now()
    start_bkk = now_bkk.replace(hour=0, minute=0, second=0, microsecond=0)
    end_bkk = start_bkk + timedelta(days=1)
    return (
        start_bkk.astimezone(timezone.utc).replace(tzinfo=None),
        end_bkk.astimezone(timezone.utc).replace(tzinfo=None),
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


def message_link(chat_id, msg_id) -> str:
    """ลิงก์กระโดดไปข้อความต้นทาง (ใช้ได้กับ supergroup เท่านั้น)"""
    chat = str(chat_id)
    if chat.startswith("-100") and msg_id:
        return f"https://t.me/c/{chat[4:]}/{msg_id}"
    return ""


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

    owner_count = len(config.SLIP_APPROVER_IDS)
    body = "\n".join(lines) if lines else "• (nobody added yet)"

    await message.reply_text(
        f"🔐 Approvers added here: {len(lines)}\n\n"
        f"{body}\n\n"
        f"Owners from config: {owner_count} — they can always approve and cannot be removed with a command."
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
        query = db.query(Transaction)
        txn = None

        if replied is not None:
            txn = query.filter(
                Transaction.chat_id == str(chat_id),
                Transaction.msg_id == str(replied.message_id),
            ).first()
            if txn is None:
                await message.reply_text(
                    "No slip is linked to that message.\n\n"
                    "Reply to the photo that carries the caption — for an album only that "
                    "one message is linked. You can also use /status <ID>."
                )
                return
        elif args:
            wanted = str(args[0]).strip()
            txn = (
                query.filter(
                    (Transaction.chat_trans_id == wanted) | (Transaction.chat_user_id == wanted)
                )
                .order_by(Transaction.created_at.desc())
                .first()
            )
            if txn is None:
                await message.reply_text(f"No slip found for ID {wanted}.")
                return
        else:
            await message.reply_text(
                "Reply to the slip photo and send /status again, "
                "or use /status <ID> with the ID written in the caption."
            )
            return

        headline = STATUS_ICONS.get(str(txn.status).lower(), f"Status: {txn.status}")
        reported = " · ".join(filter(None, [
            f"ID {txn.chat_trans_id or txn.chat_user_id or '-'}",
            txn.chat_fullname or None,
            format_thb(txn.chat_amount),
        ]))
        verified = " · ".join(filter(None, [
            txn.sender_names or None,
            format_thb(txn.api_total_amount) if txn.api_total_amount is not None else None,
            txn.receiver_account or None,
        ])) or "not verified"

        trail = []
        for entry in get_audit_trail(db, txn.batch_id):
            label = ACTION_LABELS.get(entry.action, entry.action)
            when = to_bangkok(entry.timestamp).strftime("%d-%m %H:%M")
            actor = f" — {entry.actor.split(' ', 1)[-1]}" if entry.actor else ""
            trail.append(f"  {when}  {label}{actor}")

    body = [
        headline,
        "",
        f"Reported: {reported}",
        f"Verified: {verified}",
    ]
    if trail:
        body += ["", "History:"] + trail

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

    now = get_bangkok_now()
    start_utc, end_utc = bangkok_day_range_utc(now)
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
        f"→ tab {get_sheet_name_for_datetime(now)}"
    )


def collect_sheet_health() -> dict:
    """เช็คว่าเข้าถึง Drive/ชีทได้จริงไหม — ทำงานช้าเพราะยิง API จึงต้องรันใน worker thread"""
    now = get_bangkok_now()
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
            when = job.next_run_time.astimezone(BANGKOK_TZ).strftime("%d-%m %H:%M") if job.next_run_time else "not scheduled"
            job_lines.append(f"  {job.name or job.id}: {when}")
    if not job_lines:
        job_lines = ["  (scheduler is not running)"]

    await notice.edit_text(
        "🩺 Health\n\n"
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
        "  /status — reply to a slip photo to see how it ended, or /status <ID>\n"
        "  /myid — show your Telegram ID\n"
        "  /help — this list\n\n"
        "Approvers only:\n"
        "  /pending — slips still waiting for a decision\n"
        "  /today — today's totals\n"
        "  /health — check Drive, the sheet and scheduled jobs\n"
        "  /approvers — who can approve slips\n"
        "  /approver_add — reply to someone to let them approve\n"
        "  /approver_remove — reply to someone to take it away\n\n"
        "Send a slip photo to this chat and the bot checks it automatically."
    )
