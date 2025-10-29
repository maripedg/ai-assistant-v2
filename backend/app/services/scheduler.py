from __future__ import annotations

import logging
import threading

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend.app import config as app_config
from backend.app.services.sync_orchestrator import run_sharepoint_sync

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None
_lock = threading.RLock()


def start_scheduler() -> None:
    if not app_config.sp_schedule_enabled():
        logger.info("SharePoint scheduler disabled via configuration")
        return

    cron_expression = app_config.sp_schedule_cron()
    timezone = app_config.sp_timezone()

    with _lock:
        global _scheduler  # noqa: PLW0602
        if _scheduler is not None:
            return

        scheduler = BackgroundScheduler(timezone=timezone)
        trigger = CronTrigger.from_crontab(cron_expression, timezone=timezone)
        scheduler.add_job(
            _run_scheduled_sync,
            trigger=trigger,
            id="sharepoint-sync",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info("SharePoint scheduler started with cron '%s' in timezone %s", cron_expression, timezone)


def shutdown_scheduler() -> None:
    with _lock:
        global _scheduler  # noqa: PLW0602
        if _scheduler is None:
            return
        try:
            _scheduler.shutdown(wait=False)
            logger.info("SharePoint scheduler stopped")
        finally:
            _scheduler = None


def _run_scheduled_sync() -> None:
    try:
        result = run_sharepoint_sync()
        logger.info(
            "SharePoint scheduled sync completed | sync_id=%s | job_id=%s | uploads=%s | status=%s | dir=%s",
            result.get("sync_id"),
            result.get("job_id"),
            result.get("uploads_registered"),
            result.get("status"),
            result.get("target_directory"),
        )
        if result.get("errors"):
            logger.warning("SharePoint sync reported errors: %s", result.get("errors"))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Scheduled SharePoint sync failed: %s", exc)
