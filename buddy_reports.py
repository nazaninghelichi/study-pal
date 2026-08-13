import logging
import os

from db import create_gift_token, get_all_buddy_emails, get_daily_summary
from mailer import send_progress_report

logger = logging.getLogger(__name__)

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")


async def send_buddy_reports() -> None:
    """Nightly job: emails every set accountability buddy a summary of their
    student's day, plus a one-click link to gift a sticker. Covers both web and
    Telegram users — both live in the same user_preferences table, so one query
    reaches everyone regardless of surface.
    """
    pairs = await get_all_buddy_emails()
    for user_id, buddy_email in pairs:
        try:
            summary = await get_daily_summary(user_id)
            token = await create_gift_token(user_id)
            gift_url = f"{PUBLIC_BASE_URL}/gift/{token}"
            send_progress_report(buddy_email, summary, gift_url)
            logger.info("Sent buddy report for user %s to %s", user_id, buddy_email)
        except Exception as e:
            logger.error("Failed to send buddy report for user %s: %s", user_id, e)
