import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from core.matcher import GROUP_CATEGORY
from services.gdrive import drive_service
from services.gsheets import (
    describe_expected_path,
    get_bangkok_now,
    get_month_file_name,
    get_year_folder_name,
    sheets_service,
)
from services.notifier import send_telegram_message


def create_today_sheet() -> None:
    """สร้างแท็บของวันนี้ (ใช้ตอนบอทเพิ่งบูต)"""
    sheets_service.create_sheet_for(get_bangkok_now())


def create_next_day_sheet() -> None:
    """สร้างแท็บของวันพรุ่งนี้ล่วงหน้าตั้งแต่คืนนี้

    คืนวันสิ้นเดือน 'พรุ่งนี้' จะอยู่คนละไฟล์กับวันนี้ ตัว create_sheet_for จัดการให้แล้ว
    แต่ไฟล์ของเดือนถัดไปต้องถูกสร้างไว้ก่อน ไม่งั้นงานนี้จะล้ม
    """
    target = get_bangkok_now() + timedelta(days=1)
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
logger = logging.getLogger(__name__)
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def warn_if_next_month_file_missing() -> None:
    """เตือนเข้ากลุ่มถ้าไฟล์ของเดือนหน้ายังไม่ถูกสร้าง

    รัน 22:00 ของวันสุดท้ายของเดือน (2 ชั่วโมงก่อนขึ้นเดือนใหม่)
    ถ้ามีไฟล์อยู่แล้วจะเงียบ ไม่รบกวนกลุ่ม
    """
    next_month = get_bangkok_now() + timedelta(days=1)
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


def start_scheduler() -> BackgroundScheduler:
    """Start the daily scheduler for automatic jobs."""
    scheduler = BackgroundScheduler(timezone=BANGKOK_TZ)

    try:
        existing_job_ids = {job.id for job in scheduler.get_jobs()}

        if "nightly_create_next_day_sheet" not in existing_job_ids:
            scheduler.add_job(
                create_next_day_sheet,
                trigger=CronTrigger(hour=23, minute=59, timezone=BANGKOK_TZ),
                id="nightly_create_next_day_sheet",
                name="Create tomorrow's Google Sheet tab (23:59 Bangkok)",
                replace_existing=True,
                # ดีฟอลต์ของ APScheduler คือ 1 วินาที ถ้าเครื่องติดงานหนักตอนถึงเวลา
                # งานจะถูกข้ามทั้งรอบแบบเงียบๆ — ยอมให้สายได้ถึง 1 ชั่วโมง
                misfire_grace_time=3600,
            )

        if "monthly_warn_missing_file" not in existing_job_ids:
            scheduler.add_job(
                warn_if_next_month_file_missing,
                # day="last" = วันสุดท้ายของเดือน ไม่ว่าเดือนนั้นจะมี 28/29/30/31 วัน
                trigger=CronTrigger(day="last", hour=22, minute=0, timezone=BANGKOK_TZ),
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
                run_date=datetime.now(BANGKOK_TZ),
                replace_existing=True,
                # ดีฟอลต์ของ APScheduler คือ 1 วินาที ถ้าเครื่องติดงานหนักตอนถึงเวลา
                # งานจะถูกข้ามทั้งรอบแบบเงียบๆ — ยอมให้สายได้ถึง 1 ชั่วโมง
                misfire_grace_time=3600,
            )

        scheduler.start()
        logger.info("Scheduler started successfully")
        logger.info("Server now: %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"))
        logger.info("Bangkok now: %s", datetime.now(BANGKOK_TZ).strftime("%Y-%m-%d %H:%M:%S %Z"))
        for job in scheduler.get_jobs():
            logger.info("Job '%s' next run at: %s", job.id, job.next_run_time)
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
