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


def send_magic_link(email: str, link: str) -> None:
    """Email a sign-in link. With no SMTP_HOST configured, log it instead (local dev)."""
    if not SMTP_HOST:
        logger.warning("[DEV MODE] no SMTP_HOST set — sign-in link for %s: %s", email, link)
        return

    msg = MIMEText(f"Tap to sign in to Mathoclock (link expires in 15 minutes):\n\n{link}")
    msg["Subject"] = "Your Mathoclock sign-in link"
    msg["From"] = EMAIL_FROM
    msg["To"] = email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)


def send_progress_report(buddy_email: str, summary: dict, gift_url: str | None = None) -> None:
    """Nightly accountability-buddy email. Same dev-mode fallback as send_magic_link."""
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

    if not SMTP_HOST:
        logger.warning("[DEV MODE] no SMTP_HOST set — progress report for %s to %s:\n%s", name, buddy_email, body)
        return

    msg = MIMEText(body)
    msg["Subject"] = f"{name}'s Mathoclock progress today"
    msg["From"] = EMAIL_FROM
    msg["To"] = buddy_email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
