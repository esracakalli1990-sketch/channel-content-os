"""Basic scheduler for automated tasks in Channel Content OS.

Can be run as a background process to collect analytics daily,
notify on pending approvals, and send weekly reports.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime

from .config_loader import load_config
from .notifications import notify_weekly_report
from .project import status_summary

logger = logging.getLogger(__name__)


def run_daily_tasks(root_dir) -> None:
    """Tasks that should run once a day."""
    logger.info("Running daily scheduled tasks...")
    # Example: Send a reminder for videos waiting on human approval
    rows = status_summary(root_dir)
    pending = [r for r in rows if "approved" not in r["status"] and r["status"] in ("topic", "script", "private_uploaded")]
    
    # Normally you'd notify via telegram here if there are pending items
    if pending:
        logger.info("%d videos pending human approval.", len(pending))


def run_weekly_tasks(root_dir) -> None:
    """Tasks that should run once a week (e.g., analytics reports)."""
    logger.info("Running weekly scheduled tasks...")
    # This would aggregate analytics across all videos
    # and send a Telegram report
    notify_weekly_report("Haftalık sistem ve video analitik özeti (Otomatik Rapor)")


def start_scheduler(root_dir) -> None:
    """Start a blocking loop that runs scheduled tasks.
    
    In a real production environment, you might use python-crontab,
    schedule package, or system cron. This is a basic fallback loop.
    """
    logger.info("Scheduler started. Press Ctrl+C to stop.")
    
    last_daily_run = -1
    last_weekly_run = -1
    
    try:
        while True:
            now = datetime.now(UTC)
            
            # Daily at 10:00 UTC
            if now.hour == 10 and now.day != last_daily_run:
                run_daily_tasks(root_dir)
                last_daily_run = now.day
                
            # Weekly on Monday (0)
            if now.weekday() == 0 and now.hour == 10 and now.day != last_weekly_run:
                run_weekly_tasks(root_dir)
                last_weekly_run = now.day
                
            # Sleep for 10 minutes before checking again
            time.sleep(600)
    except KeyboardInterrupt:
        logger.info("Scheduler stopped.")
