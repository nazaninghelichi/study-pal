import logging

from db import get_all_buddy_emails, get_daily_summary
from mailer import send_progress_report

logger = logging.getLogger(__name__)


async def send_buddy_reports() -> None:
    """Nightly job: emails every set accountability buddy a summary of their
    student's day. Covers both web and Telegram users — both live in the same
    user_preferences table, so one query reaches everyone regardless of surface.
    """
    pairs = await get_all_buddy_emails()
    for user_id, buddy_email in pairs:
        try:
            summary = await get_daily_summary(user_id)
            send_progress_report(buddy_email, summary)
            logger.info("Sent buddy report for user %s to %s", user_id, buddy_email)
        except Exception as e:
            logger.error("Failed to send buddy report for user %s: %s", user_id, e)
