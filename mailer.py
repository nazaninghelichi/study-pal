import os
import logging
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM", SMTP_USER or "studypal@localhost")


def _send(to: str, subject: str, body: str, dev_label: str) -> bool:
    """Shared send path. With no SMTP_HOST configured, logs instead (local dev)
    and reports failure so callers can fall back gracefully."""
    if not SMTP_HOST:
        logger.warning("[DEV MODE] no SMTP_HOST set — %s for %s:\n%s", dev_label, to, body)
        return False

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = to

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except OSError:
        logger.exception("Failed to send %s to %s", dev_label, to)
        return False


def send_progress_report(buddy_email: str, summary: dict, gift_url: str | None = None) -> bool:
    """Nightly accountability-buddy email."""
    name = summary["display_name"]
    goal, done, streak = summary["goal"], summary["done"], summary["streak"]
    pct = round(done / goal * 100) if goal else 0

    body = (
        f"Hi,\n\n"
        f"Here's {name}'s progress on Mathoclock today:\n\n"
        f"  Goal: {done}/{goal} problems ({pct}%)\n"
        f"  Current streak: {streak} day{'s' if streak != 1 else ''}\n\n"
    )
    if gift_url:
        body += f"✅ Confirm today's count and pick a sticker for {name}: {gift_url}\n\n"
    body += f"You're getting this because {name} added you as their accountability buddy."

    return _send(buddy_email, f"{name}'s Mathoclock progress today", body, "progress report")


def send_buddy_verification(buddy_email: str, student_name: str, confirm_url: str) -> bool:
    """Sent when a student adds a new accountability buddy. Nothing about the
    student's data is active until the buddy actually confirms."""
    body = (
        f"Hi,\n\n"
        f"{student_name} added you as their accountability buddy on Mathoclock. "
        f"That means once a day you'll get a short email with what they studied, "
        f"and you confirm the number, that's what makes it count.\n\n"
        f"If you're on board, confirm here (takes a few seconds):\n{confirm_url}\n\n"
        f"If you don't know why you're getting this, you can just ignore it. Nothing "
        f"happens unless you confirm."
    )
    return _send(buddy_email, f"{student_name} added you as their accountability buddy", body, "buddy verification")
