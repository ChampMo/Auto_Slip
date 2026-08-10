import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.matcher import GROUP_CATEGORY
from services.gdrive import drive_service
from services.gsheets import (
    BANGKOK_TZ,
    BUSINESS_DAY_STARTS_AT,
    BUSINESS_TZ,
    describe_expected_path,
    get_business_now,
    get_month_file_name,
    get_year_folder_name,
    sheets_service,
    to_bangkok_clock,
)
from services.notifier import send_telegram_message


def create_today_sheet() -> None:
    """สร้างแท็บของวันนี้ (ใช้ตอนบอทเพิ่งบูต)"""
    sheets_service.create_sheet_for(get_business_now())


def create_next_day_sheet() -> None:
    """สร้างแท็บของวันพรุ่งนี้ล่วงหน้าตั้งแต่คืนนี้

    คืนวันสิ้นเดือน 'พรุ่งนี้' จะอยู่คนละไฟล์กับวันนี้ ตัว create_sheet_for จัดการให้แล้ว
    แต่ไฟล์ของเดือนถัดไปต้องถูกสร้างไว้ก่อน ไม่งั้นงานนี้จะล้ม
    """
    target = get_business_now() + timedelta(days=1)
    try:
        sheets_service.create_sheet_for(target)
    except Exception as exc:
        logger.error(
            "❌ สร้างแท็บของวันที่ %s ไม่สำเร็จ: %s "
            "(ถ้าเป็นวันขึ้นเดือนใหม่ ให้เช็คว่า %s ถูกสร้างไว้ใน Drive แล้วหรือยัง)",
            target.strftime("%d-%m-%Y"),
            exc,
            describe_expected_path(target),
        )

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# httpx log URL เต็มของทุก request ซึ่งมี bot token อยู่ในนั้น
# บอท poll ทุก 10 วินาที = token ถูกเขียนลง log ตลอด 24 ชม.
# ปิดไว้ที่ WARNING ให้ยังเห็นตอนเรียก API ไม่สำเร็จ แต่ไม่รั่ว token ตอนปกติ
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def warn_if_next_month_file_missing() -> None:
    """เตือนเข้ากลุ่มถ้าไฟล์ของเดือนหน้ายังไม่ถูกสร้าง

    รัน 22:00 ของวันสุดท้ายของเดือน (2 ชั่วโมงก่อนขึ้นเดือนใหม่)
    ถ้ามีไฟล์อยู่แล้วจะเงียบ ไม่รบกวนกลุ่ม
    """
    next_month = get_business_now() + timedelta(days=1)
    folder_name = get_year_folder_name(next_month)
    file_name = get_month_file_name(next_month)

    try:
        folder_id = drive_service.get_folder_id_by_name(folder_name)
        file_id = drive_service.get_spreadsheet_id_by_name(file_name, folder_id) if folder_id else None
    except Exception as exc:
        logger.error("Could not check %s/%s in Drive: %s", folder_name, file_name, exc)
        return

    if file_id:
        logger.info("Next month's file is ready (%s/%s) — no reminder needed", folder_name, file_name)
        return

    logger.warning("Next month's file (%s/%s) is missing — notifying the groups", folder_name, file_name)

    if folder_id:
        what_to_do = (
            f"Please create an empty Google Sheets file named \"{file_name}\" "
            f"inside the existing \"{folder_name}\" folder."
        )
    else:
        what_to_do = (
            f"Please create a folder named \"{folder_name}\" in the main Drive folder, "
            f"then create an empty Google Sheets file named \"{file_name}\" inside it."
        )

    text = (
        "⚠️ Next month's sheet file is missing\n\n"
        f"In about 2 hours the bot will start writing to \"{folder_name}/{file_name}\", "
        "but it does not exist yet. Slips will fail to save until it is created.\n\n"
        f"{what_to_do}\n\n"
        "Please check:\n"
        f"• Folder name: {folder_name}\n"
        f"• File name: {file_name}\n"
        "• Names must match exactly — 2-digit month and 4-digit year (2026, not 2569)\n"
        "• Share it with the bot's service account as Editor\n\n"
        "Nothing else is needed inside the file — the bot creates the daily tabs itself.\n"
        "If you already created it, check the name and location instead of creating a second one."
    )

    for chat_id in GROUP_CATEGORY:
        send_telegram_message(chat_id, text)


def warn_about_open_slips() -> None:
    """เตือนก่อนวันธุรกิจจะเปลี่ยน ว่ายังมีสลิปค้างอยู่กี่ใบ

    สลิปที่ค้างข้ามวันจะไปลงแท็บของวันถัดไป ทำให้ยอดของวันนี้ไม่ตรงกับที่เกิดจริง
    ส่วนใบที่รอ QR อยู่จะไม่โผล่ใน /today เลย — เงินเข้าจริงแต่ไม่มีในชีท
    และไม่มีอะไรสะดุดถ้าไม่มีใครทวง
    """
    from bot.commands import OPEN_STATUSES
    from core.matcher import NEEDS_QR_STATUS
    from database.models import Transaction
    from database.session import SessionLocal

    with SessionLocal() as db:
        open_slips = db.query(Transaction).filter(
            Transaction.status.in_(OPEN_STATUSES)).all()
        by_chat = {}
        for slip in open_slips:
            bucket = by_chat.setdefault(str(slip.chat_id), {"total": 0, "needs_qr": 0})
            bucket["total"] += 1
            if str(slip.status) == NEEDS_QR_STATUS:
                bucket["needs_qr"] += 1

    for chat_id, counts in by_chat.items():
        if chat_id not in GROUP_CATEGORY:
            continue
        lines = [
            f"⏰ {counts['total']} slip(s) still open",
            "",
            "The business day changes in one hour. Anything left open now will land on "
            "tomorrow's tab instead of today's.",
        ]
        if counts["needs_qr"]:
            lines.append(
                f"\n{counts['needs_qr']} of them are still waiting for a QR code — "
                "those are not counted anywhere until someone sends it or reports the QR unreadable."
            )
        lines.append("\nRun /pending to see the list.")
        send_telegram_message(chat_id, "\n".join(lines))
        logger.info("Warned about open slips | chat_id=%s | open=%s | needs_qr=%s",
                    chat_id, counts["total"], counts["needs_qr"])


# จำไว้ว่าเคยแจ้งไปแล้วหรือยัง จะได้ไม่ยิงซ้ำทุก 5 นาทีจนกลุ่มรก
_relay_alerted = {"down": False}


def watch_relay() -> None:
    """เฝ้าดูตัวฟัง ถ้าหลุดให้แจ้งในกลุ่มทันที

    ตัวฟังหลุดแล้วเงียบคือความเสี่ยงที่ใหญ่ที่สุดของทางนี้ — ทุกอย่างดูปกติหมด
    บอทยังตอบคำสั่งได้ กลุ่มยังเดินอยู่ แต่สลิปจากบอทตัวอื่นไม่เข้าระบบเลย
    ถ้าไม่มีใครพิมพ์ /health ก็จะไม่มีใครรู้จนกว่าจะกระทบยอดปลายวัน
    """
    from services.relay import relay_status

    status = relay_status()
    if not status["enabled"]:
        return

    if status["connected"]:
        if _relay_alerted["down"]:
            _relay_alerted["down"] = False
            logger.info("Relay is back")
            for chat_id in GROUP_CATEGORY:
                send_telegram_message(chat_id, "📡 ตัวฟังกลับมาทำงานแล้ว")
        return

    if _relay_alerted["down"]:
        return   # แจ้งไปแล้ว รอจนกว่าจะกลับมาค่อยแจ้งใหม่

    _relay_alerted["down"] = True
    logger.warning("Relay is disconnected — telling the groups")
    for chat_id in GROUP_CATEGORY:
        send_telegram_message(
            chat_id,
            "⚠️ ตัวฟังข้อความหลุด\n\n"
            "สลิปที่บอทตัวอื่นโพสต์จะไม่เข้าระบบจนกว่าจะแก้\n"
            "ระหว่างนี้ให้ฟอร์เวิร์ดสลิปเข้ากลุ่มเองไปก่อน\n\n"
            "ดูสถานะด้วย /health",
        )


def start_scheduler() -> BackgroundScheduler:
    """Start the daily scheduler for automatic jobs."""
    # ตั้งเวลาตามปฏิทินธุรกิจ ไม่ใช่นาฬิกาไทย งานสิ้นเดือนจึงตรงกับเดือนที่ระบบใช้จริง
    scheduler = BackgroundScheduler(timezone=BUSINESS_TZ)

    try:
        existing_job_ids = {job.id for job in scheduler.get_jobs()}

        if "nightly_create_next_day_sheet" not in existing_job_ids:
            scheduler.add_job(
                create_next_day_sheet,
                # 23:59 เวลาธุรกิจ = 22:59 นาฬิกาไทย คือ 1 นาทีก่อนวันธุรกิจใหม่เริ่ม
                trigger=CronTrigger(hour=23, minute=59, timezone=BUSINESS_TZ),
                id="nightly_create_next_day_sheet",
                name="Create tomorrow's Google Sheet tab",
                replace_existing=True,
                # ดีฟอลต์ของ APScheduler คือ 1 วินาที ถ้าเครื่องติดงานหนักตอนถึงเวลา
                # งานจะถูกข้ามทั้งรอบแบบเงียบๆ — ยอมให้สายได้ถึง 1 ชั่วโมง
                misfire_grace_time=3600,
            )

        if "watch_relay" not in existing_job_ids:
            scheduler.add_job(
                watch_relay,
                # ทุก 5 นาที ถี่พอที่จะรู้เร็ว แต่ไม่ถี่จนเปลืองอะไร
                trigger="interval", minutes=5,
                id="watch_relay",
                name="Check that the relay is still connected",
                replace_existing=True,
                misfire_grace_time=300,
            )

        if "nightly_warn_open_slips" not in existing_job_ids:
            scheduler.add_job(
                warn_about_open_slips,
                # 23:00 เวลาธุรกิจ = 22:00 นาฬิกาไทย คือ 1 ชั่วโมงก่อนวันใหม่เริ่ม
                # ให้เวลาพอที่จะเคลียร์ของค้างได้ทัน แต่ไม่เช้าจนคนลืมไปแล้ว
                trigger=CronTrigger(hour=23, minute=0, timezone=BUSINESS_TZ),
                id="nightly_warn_open_slips",
                name="Warn the groups about slips still open before the day changes",
                replace_existing=True,
                misfire_grace_time=3600,
            )

        if "monthly_warn_missing_file" not in existing_job_ids:
            scheduler.add_job(
                warn_if_next_month_file_missing,
                # day="last" = วันสุดท้ายของเดือน ไม่ว่าเดือนนั้นจะมี 28/29/30/31 วัน
                # นับตามปฏิทินธุรกิจ จึงตรงกับเดือนของไฟล์ชีทที่ระบบจะเขียนจริง
                trigger=CronTrigger(day="last", hour=22, minute=0, timezone=BUSINESS_TZ),
                id="monthly_warn_missing_file",
                name="Warn the groups 2 hours before the new month if its file is missing",
                replace_existing=True,
                # ดีฟอลต์ของ APScheduler คือ 1 วินาที ถ้าเครื่องติดงานหนักตอนถึงเวลา
                # งานจะถูกข้ามทั้งรอบแบบเงียบๆ — ยอมให้สายได้ถึง 1 ชั่วโมง
                misfire_grace_time=3600,
            )

        if "startup_create_today_sheet" not in existing_job_ids:
            scheduler.add_job(
                create_today_sheet,
                id="startup_create_today_sheet",
                name="Create today's Google Sheet tab at startup",
                trigger="date",
                run_date=get_business_now(),
                replace_existing=True,
                # ดีฟอลต์ของ APScheduler คือ 1 วินาที ถ้าเครื่องติดงานหนักตอนถึงเวลา
                # งานจะถูกข้ามทั้งรอบแบบเงียบๆ — ยอมให้สายได้ถึง 1 ชั่วโมง
                misfire_grace_time=3600,
            )

        scheduler.start()
        logger.info("Scheduler started successfully")
        logger.info("Server now: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"))
        logger.info("Bangkok clock: %s", datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
        logger.info(
            "Business day: %s (a new day starts at %s Bangkok time)",
            get_business_now().strftime("%Y-%m-%d"), BUSINESS_DAY_STARTS_AT,
        )
        for job in scheduler.get_jobs():
            # แสดงเป็นนาฬิกาไทย เพราะคนอ่าน log เทียบกับนาฬิกาบนกำแพง
            when = to_bangkok_clock(job.next_run_time) if job.next_run_time else None
            logger.info(
                "Job '%s' next run at: %s (Bangkok time)",
                job.id, when.strftime("%Y-%m-%d %H:%M") if when else "not scheduled",
            )
        return scheduler
    except Exception as exc:
        logger.exception("Failed to start scheduler: %s", exc)
        scheduler.shutdown(wait=False)
        raise


def run_now() -> None:
    """Run the scheduled job immediately for testing."""
    try:
        logger.info("Running scheduled job manually")
        create_today_sheet()
    except Exception as exc:
        logger.exception("Manual scheduled job failed: %s", exc)
