import logging
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from services.gsheets import sheets_service


def create_today_sheet() -> None:
    """Use the existing sheet creation logic from the legacy Google Sheets service."""
    sheets_service.create_today_sheet()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def start_scheduler() -> BackgroundScheduler:
    """Start the daily scheduler for automatic jobs."""
    scheduler = BackgroundScheduler(timezone="Asia/Bangkok")

    try:
        scheduler.add_job(
            create_today_sheet,
            trigger=CronTrigger(hour=1, minute=0, timezone="Asia/Bangkok"),
            id="daily_create_today_sheet",
            name="Create today's Google Sheet tab",
            replace_existing=True,
        )

        scheduler.add_job(
            create_today_sheet,
            id="startup_create_today_sheet",
            name="Create today's Google Sheet tab at startup",
            trigger="date",
            run_date=datetime.now(),
            replace_existing=True,
        )

        scheduler.start()
        logger.info("Scheduler started successfully")
        logger.info("Daily sheet creation job is scheduled for 01:00 Asia/Bangkok")
        logger.info("Next scheduled execution: %s", scheduler.get_jobs()[0].next_run_time)
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
