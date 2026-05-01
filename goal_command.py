import logging
import os
import requests
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CommandHandler, CallbackQueryHandler, ConversationHandler, ContextTypes
from config import GIPHY_API_KEY
from db import get_pg_conn as get_db_connection

logger = logging.getLogger(__name__)
load_dotenv()

# Conversation states
AWAIT_GOAL, LOGGING = range(2)

# Button config
BUTTON_PREFIX = "setgoal_"
BUTTON_STEPS = [5, 10, 15]
LOG_PREFIX = "logstudy_"
LOG_DONE = "done"
CANCEL = "cancel"

# =========================================
# Database Helpers
# =========================================
async def get_or_create_today(user_id: int) -> tuple[int, int]:
    today = date.today().isoformat()
    conn = await get_db_connection()
    row = await conn.fetchrow(
        "SELECT goal, done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, today
    )
    if row:
        await conn.close()
        return row['goal'], row['done']
    last = await conn.fetchrow(
        "SELECT goal FROM daily_track WHERE user_id=$1 ORDER BY date DESC LIMIT 1", user_id
    )
    goal = last['goal'] if last else 0
    done = 0
    await conn.execute(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4)",
        user_id, today, goal, done
    )
    await conn.close()
    return goal, done

async def set_goal(user_id: int, new_goal: int):
    today = date.today().isoformat()
    conn = await get_db_connection()
    done_row = await conn.fetchrow(
        "SELECT done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, today
    )
    done = done_row['done'] if done_row else 0
    await conn.execute(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, date) DO UPDATE SET goal = EXCLUDED.goal, done = EXCLUDED.done",
        user_id, today, new_goal, done
    )
    await conn.close()

async def fetch_count(user_id: int) -> int:
    _, done = await get_or_create_today(user_id)
    return done

async def update_count(user_id: int, new_done: int):
    today = date.today().isoformat()
    conn = await get_db_connection()
    goal_row = await conn.fetchrow(
        "SELECT goal FROM daily_track WHERE user_id=$1 AND date=$2", user_id, today
    )
    goal = goal_row['goal'] if goal_row else 0
    await conn.execute(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, date) DO UPDATE SET done = EXCLUDED.done",
        user_id, today, goal, new_done
    )
    await conn.close()

# =========================================
# Cancel Handler
# =========================================
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handles cancellation and ends any ongoing conversation."""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Operation cancelled.", reply_markup=None)
    else:
        await update.message.reply_text("❌ Operation cancelled.")
    return ConversationHandler.END

# =========================================
# /setgoal Conversation
# =========================================
async def start_setgoal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    logger.info(f"/setgoal initiated by user {user_id}")
    goal, _ = await get_or_create_today(user_id)
    keyboard = [
        [InlineKeyboardButton(str(step), callback_data=f"{BUTTON_PREFIX}{step}") for step in BUTTON_STEPS],
        [InlineKeyboardButton("🏠 Home", callback_data=CANCEL)]
    ]
    text = f"🔢 Your current daily goal is *{goal}*\n\nChoose a new one:"
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return AWAIT_GOAL

async def handle_setgoal_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    data = q.data
    logger.info(f"handle_setgoal_choice: user {user_id} selected {data}")
    new_goal = int(data.replace(BUTTON_PREFIX, '')) if data.startswith(BUTTON_PREFIX) else None
    if new_goal is None:
        # Cancel
        await q.edit_message_text("❌ Goal setting cancelled.")
        return ConversationHandler.END
    await set_goal(user_id, new_goal)
    await q.edit_message_text(f"✅ Daily goal set to *{new_goal}*!", parse_mode="Markdown")
    return ConversationHandler.END

def get_setgoal_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler('setgoal', start_setgoal)],
        states={
            AWAIT_GOAL: [
                CallbackQueryHandler(handle_setgoal_choice, pattern=f"^{BUTTON_PREFIX}\d+$"),
            ]
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern=f"^{CANCEL}$")],
        allow_reentry=True
    )

# =========================================
# /logstudies Conversation
# =========================================
def build_log_ui(done: int, goal: int) -> str:
    bar = '✅' * min(done, goal) + '⬜️' * max(0, goal - done)
    extra = f" +{done - goal} ✨" if done > goal else ''
    return f"📦 Today: {done}\n🌟 Goal: {goal}\n{bar}{extra}"

async def start_logstudies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    logger.info(f"/logstudies initiated by user {user_id}")
    goal, done = await get_or_create_today(user_id)
    keyboard = [
        [InlineKeyboardButton('➕ Log one', callback_data=f"{LOG_PREFIX}inc")],
        [InlineKeyboardButton('🏁 Done Logging', callback_data=f"{LOG_PREFIX}{LOG_DONE}")],
        [InlineKeyboardButton('🏠 Home', callback_data=CANCEL)]
    ]
    await update.message.reply_text(build_log_ui(done, goal), reply_markup=InlineKeyboardMarkup(keyboard))
    return LOGGING

async def log_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    action = q.data
    logger.info(f"log_button triggered for user {user_id} with action {action}")
    if action == f"{LOG_PREFIX}inc":
        done = await fetch_count(user_id) + 1
        await update_count(user_id, done)
        goal, _ = await get_or_create_today(user_id)
        await q.edit_message_text(build_log_ui(done, goal), reply_markup=q.message.reply_markup)
        return LOGGING
    if action == f"{LOG_PREFIX}{LOG_DONE}":
        await q.edit_message_text('🎉 Logged! Great work today.', reply_markup=None)
        # Send celebratory GIF if available
        try:
            resp = requests.get(
                'https://api.giphy.com/v1/gifs/random',
                params={'api_key': GIPHY_API_KEY, 'tag': 'hustle cat', 'rating': 'pg'},
                timeout=5
            )
            data = resp.json().get('data', {})
            gif_url = data.get('images', {}).get('original', {}).get('url')
        except Exception as e:
            logger.warning(f"Giphy API error: {e}")
            gif_url = None
        if gif_url:
            await context.bot.send_animation(chat_id=user_id, animation=gif_url)
        # After celebrating, offer to return home
        await context.bot.send_message(
            chat_id=user_id,
            text='🏠 Back to home:',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🏠 Home', callback_data=CANCEL)]])
        )
        return ConversationHandler.END
    # If somehow reached here, cancel
    return await cancel(update, context)

def get_logstudies_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler('logstudies', start_logstudies)],
        states={
            LOGGING: [
                CallbackQueryHandler(log_button, pattern=f"^{LOG_PREFIX}(inc|{LOG_DONE})$"),
            ]
        },
        fallbacks=[CallbackQueryHandler(cancel, pattern=f"^{CANCEL}$")],
        allow_reentry=True
    )

# =========================================
# /progress Command
# =========================================
async def progress(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    today_date = datetime.now().date()
    start_week = today_date - timedelta(days=today_date.weekday())
    week_dates = [(start_week + timedelta(days=i)).isoformat() for i in range(7)]

    conn = await get_db_connection()
    lines = []
    total_goal = total_done = streak = 0
    on_streak = True
    for d in week_dates:
        row = await conn.fetchrow(
            "SELECT goal, done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, d
        )
        g, dn = (row['goal'], row['done']) if row else (0, 0)
        total_goal += g
        total_done += dn
        if g and dn >= g and on_streak:
            streak += 1
        else:
            on_streak = False
        bar = '✅' * min(dn, g) + '⬜️' * max(0, g - dn)
        extra = f" +{dn - g} ✨" if dn > g else ''
        day_name = datetime.fromisoformat(d).strftime('%A')
        emoji = '✅' if dn >= g else '❌'
        lines.append(f"{emoji} {day_name}: {bar}{extra} ({dn}/{g})")
    await conn.close()

    pct = round((total_done / total_goal * 100), 1) if total_goal else 0
    streak_line = f"\n🔥 Current streak: **{streak}** day(s)!" if streak else ''
    text = (
        '📊 **Weekly Progress**\n' +
        '\n'.join(lines) +
        f"\n\n**Total:** {total_done}/{total_goal} ({pct}%)" +
        streak_line
    )
    await update.message.reply_text(text, parse_mode='Markdown')
