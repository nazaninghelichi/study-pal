import sqlite3
from telegram import Update
from telegram.ext import (
    CommandHandler, MessageHandler,
    ConversationHandler, CallbackContext, filters
)
import logging

logger = logging.getLogger(__name__)

ASK_USERNAME = range(1)

# === DB INIT ===
def init_buddy_system_table():
    try:
        conn = sqlite3.connect("studypal.db")
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS buddies (
                user_id INTEGER PRIMARY KEY,
                buddy_username TEXT,
                buddy_chat_id INTEGER
            )
        """)
        conn.commit()
        conn.close()
        logger.info("Buddy system table initialized successfully.")
    except Exception as e:
        logger.error(f"Error initializing buddy table: {e}")

# === Invite Flow ===
def invite_buddy_start(update: Update, context: CallbackContext):
    update.message.reply_text("👯 Who’s your accountability buddy? Please send their @username.")
    return ASK_USERNAME

def receive_username(update: Update, context: CallbackContext):
    user_id = update.message.chat.id
    username = update.message.text.strip().lstrip("@")

    try:
        conn = sqlite3.connect("studypal.db")
        c = conn.cursor()
        c.execute("""
            INSERT OR REPLACE INTO buddies (user_id, buddy_username)
            VALUES (?, ?)
        """, (user_id, username))
        conn.commit()
        conn.close()

        update.message.reply_text(
            f"✅ Got it! We'll nudge @{username} when you log — and vice versa."
        )
    except Exception as e:
        logger.error(f"Error saving buddy for user {user_id}: {e}")
        update.message.reply_text("❌ Couldn't save buddy. Try again later.")
    return ConversationHandler.END

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("❌ Buddy setup cancelled.")
    return ConversationHandler.END

# === Buddy Status Check ===
def my_buddy(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    try:
        conn = sqlite3.connect("studypal.db")
        c = conn.cursor()
        c.execute("SELECT buddy_username FROM buddies WHERE user_id = ?", (user_id,))
        row = c.fetchone()
        conn.close()

        if row and row[0]:
            update.message.reply_text(f"👯 Your current buddy is: @{row[0]}")
        else:
            update.message.reply_text("You don't have a buddy yet. Use /invitebuddy to set one.")
    except Exception as e:
        logger.error(f"Error fetching buddy info for {user_id}: {e}")
        update.message.reply_text("❌ Error retrieving buddy info.")

# === Unbuddy ===
def unbuddy(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    try:
        conn = sqlite3.connect("studypal.db")
        c = conn.cursor()
        c.execute("DELETE FROM buddies WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
        update.message.reply_text("👋 You’re no longer paired with a buddy.")
    except Exception as e:
        logger.error(f"Error unpairing buddy for {user_id}: {e}")
        update.message.reply_text("❌ Error removing buddy. Try again later.")

# === Export Handlers ===
def get_invite_buddy_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("invitebuddy", invite_buddy_start)],
        states={
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_username)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        name="invite_buddy_conversation",
        persistent=False
    )
