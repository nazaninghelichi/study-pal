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

    msg = MIMEText(f"Tap to sign in to Study-Pal (link expires in 15 minutes):\n\n{link}")
    msg["Subject"] = "Your Study-Pal sign-in link"
    msg["From"] = EMAIL_FROM
    msg["To"] = email

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
