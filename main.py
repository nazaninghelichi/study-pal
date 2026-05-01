import logging
import os
import asyncio
from datetime import date
from keep_alive import keep_alive
keep_alive()
from dotenv import load_dotenv
load_dotenv()

from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

from goal_command import (
    get_setgoal_handler,
    get_logstudies_handler,
    progress
)
from username_command import get_setname_handler
from leaderboard_command import leaderboard as leaderboard_actual
from config import TELEGRAM_BOT_TOKEN
from reminders import register_reminders
from db import get_pg_conn, init_db_pg
from seed_daily_funny_data import seed_funny_data
from wrapup import send_wrapup

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# === Keyboards ===
HOME_KB = ReplyKeyboardMarkup([['\ud83c\udfe0 Home']], resize_keyboard=True)

# === Core Handlers ===
async def wrapup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    display_name = update.effective_user.first_name or str(chat_id)
    chat_names = {chat_id: display_name}
    await send_wrapup(context.application, [chat_id], chat_names)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO users(user_id, username, first_name) VALUES($1, $2, $3) "
        "ON CONFLICT (user_id) DO UPDATE SET first_name = EXCLUDED.first_name",
        user_id, '', user.first_name or ''
    )
    row = await conn.fetchrow(
        "SELECT COALESCE(NULLIF(username, ''), first_name) AS display_name "
        "FROM users WHERE user_id = $1",
        user_id
    )
    display_name = row['display_name'] if row and row['display_name'] else 'there'

    today = date.today().isoformat()
    row2 = await conn.fetchrow(
        "SELECT goal FROM daily_track WHERE user_id = $1 AND date = $2",
        user_id, today
    )
    has_goal = bool(row2 and row2['goal'] > 0)
    await conn.close()

    tip = ""
    if not has_goal:
        tip = "\n\n\u26a0\ufe0f _Tip: Set your daily goal using_ `/setgoal` _to unlock full tracking._"

    main_kb = ReplyKeyboardMarkup([
        ['/logstudies', '/setgoal'],
        ['/leaderboard', '/progress'],
        ['/settings']
    ], resize_keyboard=True)

    await update.message.reply_text(
        f"\ud83d\udc4b Welcome back, {display_name}!\n\nHere\u2019s what you can do:\n"
        "\u2022 `/logstudies` — Log your applications\n"
        "\u2022 `/setgoal` — Set or change your daily goal\n"
        "\u2022 `/leaderboard` — See today’s top studiers\n"
        "\u2022 `/progress` — See your weekly progress\n"
        "\u2022 `/settings` — Configure name, reminders, and more" + tip,
        reply_markup=main_kb,
        parse_mode="Markdown"
    )
    logger.info(f"/start triggered by {display_name} ({user_id})")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\u2753 *Help*\nUse /settings to view options, or tap \ud83c\udfe0 Home to return to main menu.",
        reply_markup=HOME_KB,
        parse_mode="Markdown"
    )

async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "\ud83d\udcac *About This Bot*\n\n"
        "I’m also studying hard right now, so I understand how frustrating it can feel.\n\n"
        "This bot helps us track progress and stay consistent — in a fun, supportive way.\n\n"
        "Wishing *you* (and *me*) the best of luck! \ud83c\udf40\n\n"
        "\ud83d\udce9 Feedback: calpal.agent@gmail.com",
        reply_markup=HOME_KB,
        parse_mode="Markdown"
    )

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings_kb = ReplyKeyboardMarkup([
        ['/setname', '/reminders'],
        ['/about', '/help'],
        ['\ud83c\udfe0 Home']
    ], resize_keyboard=True)
    await update.message.reply_text(
        "\u2699\ufe0f *Settings*\n\n"
        "\u2022 `/setname` — Change your display name\n"
        "\u2022 `/reminders` — Toggle reminders on or off\n"
        "\u2022 `/about` — About this bot\n"
        "\u2022 `/help` — Show help info\n",
        reply_markup=settings_kb,
        parse_mode="Markdown"
    )

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await leaderboard_actual(update, context)
    await update.message.reply_text(
        "\ud83c\udfe0 Tap Home to return to the main menu.",
        reply_markup=HOME_KB
    )

async def progress_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await progress(update, context)
    await update.message.reply_text(
        "\ud83c\udfe0 Tap Home to return to the main menu.",
        reply_markup=HOME_KB
    )

async def toggle_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.callback_query.from_user.id if update.callback_query else update.effective_user.id
    logger.info(f"\ud83d\udd14 toggle_reminders triggered by user {user_id!r}, callback={bool(update.callback_query)}")
    if update.callback_query:
        await update.callback_query.answer()

    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT reminders_enabled FROM user_preferences WHERE user_id = $1",
        user_id
    )
    new_state = not bool(row and row['reminders_enabled'])
    await conn.execute(
        "INSERT INTO user_preferences(user_id, reminders_enabled) VALUES($1,$2) "
        "ON CONFLICT (user_id) DO UPDATE SET reminders_enabled = EXCLUDED.reminders_enabled",
        user_id, new_state
    )
    await conn.close()

    status = "ON" if new_state else "OFF"
    text = (
        f"\ud83d\udd14 Reminders are now *{status}*. I will send you reminders at 09:00, 15:00, and 21:00 daily."
    )
    btn_label = "Turn OFF" if new_state else "Turn ON"
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton(btn_label, callback_data="toggle_reminders")]])

    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)

async def testdb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"/testdb triggered by user {update.effective_user.id}")
    try:
        conn = await get_pg_conn()
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public';"
        )
        names = ", ".join(r['table_name'] for r in rows)
        await update.message.reply_text(f"\u2705 Connected! Tables: {names}")
        await conn.close()
    except Exception as e:
        logger.error(f"/testdb error: {e}")
        await update.message.reply_text(f"\u274c Connection failed: {e}")

# === Bot Setup and Run ===
if __name__ == "__main__":
    logger.info("\ud83d\udd25 Running Study-Pal…")
    asyncio.get_event_loop().run_until_complete(init_db_pg())

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # Register handlers
    app.add_handler(MessageHandler(filters.Regex(r"^\ud83c\udfe0 Home$"), start))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("settings", settings_command))
    app.add_handler(CommandHandler("reminders", toggle_reminders))
    app.add_handler(CallbackQueryHandler(toggle_reminders, pattern="^toggle_reminders$"))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("about", about))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("progress", progress_handler))
    app.add_handler(CommandHandler("wrapup", wrapup_command))
    app.add_handler(CommandHandler("testdb", testdb))
    app.add_handler(get_setgoal_handler())
    app.add_handler(get_logstudies_handler())
    app.add_handler(get_setname_handler())
    app.add_handler(CallbackQueryHandler(start, pattern="^cancel$"))

    # Configure JobQueue timezone and schedule reminders
    from zoneinfo import ZoneInfo
    app.job_queue.scheduler.configure(timezone=ZoneInfo("America/Toronto"))
    asyncio.get_event_loop().run_until_complete(register_reminders(app.job_queue))

    # === Schedule Daily Wrap-up ===
    from db import get_user_profiles
    from wrapup import send_wrapup
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    async def run_daily_wrapup():
        user_profiles = await get_user_profiles()
        chat_ids = list(user_profiles.keys())
        conn = await get_pg_conn()
        rows = await conn.fetch(
            "SELECT user_id, COALESCE(NULLIF(username, ''), first_name) AS name FROM users WHERE user_id = ANY($1::BIGINT[])",
            chat_ids
        )
        await conn.close()
        chat_names = {r["user_id"]: r["name"] for r in rows}
        await send_wrapup(app, chat_ids, chat_names, user_profiles)

    scheduler = AsyncIOScheduler(timezone=ZoneInfo("America/Toronto"))
    scheduler.add_job(lambda: asyncio.create_task(run_daily_wrapup()), trigger="cron", hour=22, minute=0)
    scheduler.start()

    logger.info("\ud83e\udd16 Study-Pal is live! Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)

    # Fake user seed
    app.job_queue.run_repeating(
        lambda ctx: asyncio.create_task(seed_funny_data()),
        interval=6 * 3600,  # every six hours
        first=0,             # run immediately on startup
        name="seed-fake-data-6h"
    )
