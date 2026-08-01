import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from services.gsheets import sheets_service


def create_today_sheet() -> None:
    """Use the existing sheet creation logic from the legacy Google Sheets service."""
    sheets_service.create_today_sheet()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
BANGKOK_TZ = ZoneInfo("Asia/Bangkok")


def start_scheduler() -> BackgroundScheduler:
    """Start the daily scheduler for automatic jobs."""
    scheduler = BackgroundScheduler(timezone=BANGKOK_TZ)

    try:
        existing_job_ids = {job.id for job in scheduler.get_jobs()}

        if "daily_create_today_sheet" not in existing_job_ids:
            scheduler.add_job(
                create_today_sheet,
                trigger=CronTrigger(hour=2, minute=3, timezone=BANGKOK_TZ),
                id="daily_create_today_sheet",
                name="Create today's Google Sheet tab",
                replace_existing=True,
            )

        if "startup_create_today_sheet" not in existing_job_ids:
            scheduler.add_job(
                create_today_sheet,
                id="startup_create_today_sheet",
                name="Create today's Google Sheet tab at startup",
                trigger="date",
                run_date=datetime.now(BANGKOK_TZ),
                replace_existing=True,
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
