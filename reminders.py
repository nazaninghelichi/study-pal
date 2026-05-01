import logging
from datetime import time, date
from telegram import Update
from telegram.ext import ContextTypes
from db import get_db_connection
from config import GIPHY_API_KEY
import requests

logger = logging.getLogger(__name__)

# =========================================
# Helper to fetch display name and stats via Postgres
# =========================================
async def _get_user_info(user_id: int):
    conn = await get_db_connection()
    # Fetch display name
    row = await conn.fetchrow(
        "SELECT COALESCE(NULLIF(username, ''), first_name) AS display_name "
        "FROM users WHERE user_id = $1",
        user_id
    )
    display_name = row['display_name'] if row else 'there'

    # Fetch today's goal and done
    today_iso = date.today().isoformat()
    row2 = await conn.fetchrow(
        "SELECT goal, done FROM daily_track WHERE user_id = $1 AND date = $2",
        user_id, today_iso
    )
    if row2:
        goal, done = row2['goal'], row2['done']
    else:
        goal, done = 0, 0

    await conn.close()
    return display_name, goal, done

# =========================================
# Helper to fetch a random GIF from Giphy
# =========================================
async def _get_random_gif(tag: str):
    try:
        resp = requests.get(
            "https://api.giphy.com/v1/gifs/random",
            params={"api_key": GIPHY_API_KEY, "tag": tag, "rating": "pg"},
            timeout=5
        )
        data = resp.json().get("data", {})
        return data.get("images", {}).get("original", {}).get("url")
    except Exception as e:
        logger.warning(f"Giphy API error for tag '{tag}': {e}")
        return None

# =========================================
# Reminder Callbacks (async)  
# =========================================
async def morning_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Send morning reminder at 09:00."""
    job = context.job
    user_id = job.chat_id

    display_name, goal, _ = await _get_user_info(user_id)
    text = f"😺 Good morning, {display_name}! You have a goal of {goal} questions to complete today."
    await context.bot.send_message(chat_id=user_id, text=text)

    gif_url = await _get_random_gif("come catch me cat")
    if gif_url:
        await context.bot.send_animation(chat_id=user_id, animation=gif_url)

async def afternoon_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Send afternoon reminder at 15:00."""
    job = context.job
    user_id = job.chat_id

    display_name, goal, done = await _get_user_info(user_id)
    text = f"🐱 How’s the hunt, {display_name}? {done} logged out of {goal} so far—keep going!"
    await context.bot.send_message(chat_id=user_id, text=text)

    gif_url = await _get_random_gif("typing cat")
    if gif_url:
        await context.bot.send_animation(chat_id=user_id, animation=gif_url)

async def evening_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Send evening reminder at 21:00."""
    job = context.job
    user_id = job.chat_id

    display_name, goal, done = await _get_user_info(user_id)
    text = f"🌠 Final call, {display_name}! You've logged {done}/{goal}. Last chance before leaderboard!"
    await context.bot.send_message(chat_id=user_id, text=text)

    gif_url = await _get_random_gif("Sleepy Cat Dont Bug Me")
    if gif_url:
        await context.bot.send_animation(chat_id=user_id, animation=gif_url)

# =========================================
# Registration (per-user via Postgres preferences)
# =========================================
async def register_reminders(job_queue):
    # Fetch users with reminders enabled
    conn = await get_db_connection()
    rows = await conn.fetch(
        "SELECT user_id FROM user_preferences WHERE reminders_enabled = TRUE"
    )
    await conn.close()

    # Configure scheduler to use Toronto timezone once
    try:
        from zoneinfo import ZoneInfo
        job_queue.scheduler.configure(timezone=ZoneInfo("America/Toronto"))
    except Exception as e:
        logger.warning(f"Scheduler timezone config failed: {e}")

    # Schedule daily reminders in local time
    for r in rows:
        uid = r['user_id']
        job_queue.run_daily(
            morning_reminder,
            time=time(hour=9, minute=0),
            chat_id=uid
        )
        job_queue.run_daily(
            afternoon_reminder,
            time=time(hour=15, minute=0),
            chat_id=uid
        )
        job_queue.run_daily(
            evening_reminder,
            time=time(hour=21, minute=0),
            chat_id=uid
        )
