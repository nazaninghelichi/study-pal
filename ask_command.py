import requests
import sqlite3
import datetime
import httpx
import json
import logging
from telegram import Update
from telegram.ext import CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes
from config import OPENROUTER_API_KEY  # 🔐 Updated

# --- ADD THIS LOGGING SETUP ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO # Or DEBUG
)
logger = logging.getLogger(__name__)
# ------------------------------

# ========== LLM CALL ==========
async def ask_studypal_ai(question: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "mistralai/mistral-7b-instruct",
        "messages": [
            {"role": "system", "content": "You are Study-Pal, a tough love study coach who gives direct, motivational advice with a military/coach style tone. Use phrases like 'soldier', 'recruit', 'let's crush this', etc."},
            {"role": "user", "content": question}
        ]
    }
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=60.0
            )
            response.raise_for_status()

        response_data = response.json()

        if "choices" in response_data and len(response_data["choices"]) > 0:
            message = response_data["choices"][0].get("message", {})
            content = message.get("content")
            if content:
                return content
            else:
                logger.error("AI response missing 'content'. Full response: %s", response_data)
                raise ValueError("AI response format error: missing content")
        else:
            logger.error("AI response missing 'choices'. Full response: %s", response_data)
            raise ValueError("AI response format error: missing choices")

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter API request failed: {e.response.status_code} - {e.response.text}")
        raise ValueError(f"AI Service Error ({e.response.status_code})")
    except httpx.RequestError as e:
        logger.error(f"Network error connecting to OpenRouter API: {e}")
        raise ConnectionError("Network error contacting AI service")
    except Exception as e:
        logger.error(f"Error processing AI response in ask_studypal_ai: {e}", exc_info=True)
        raise ValueError("Invalid response/error processing AI result")

# ========== DB INIT ==========
def init_question_db():
    conn = sqlite3.connect("studypal.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_questions (
            user_id INTEGER,
            date TEXT,
            count INTEGER
        )
    """)
    conn.commit()
    conn.close()

def init_goal_and_progress_tables():
    conn = sqlite3.connect("studypal.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_goals (
            user_id INTEGER,
            weekday TEXT,
            goal_count INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS user_progress (
            user_id INTEGER,
            date TEXT,
            count_applied INTEGER
        )
    """)
    conn.commit()
    conn.close()

# ========== LIMIT CHECK ==========
def check_question_limit(user_id: int) -> bool:
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect("studypal.db")
    c = conn.cursor()
    c.execute("SELECT count FROM user_questions WHERE user_id = ? AND date = ?", (user_id, today))
    row = c.fetchone()
    conn.close()
    return row and row[0] >= 2

def log_question(user_id: int):
    today = datetime.date.today().isoformat()
    conn = sqlite3.connect("studypal.db")
    c = conn.cursor()
    c.execute("SELECT count FROM user_questions WHERE user_id = ? AND date = ?", (user_id, today))
    row = c.fetchone()
    if row:
        c.execute("UPDATE user_questions SET count = count + 1 WHERE user_id = ? AND date = ?", (user_id, today))
    else:
        c.execute("INSERT INTO user_questions (user_id, date, count) VALUES (?, ?, 1)", (user_id, today))
    conn.commit()
    conn.close()

# ========== CONVERSATION HANDLER ==========
ASKING = range(1)

async def ask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    if check_question_limit(user_id):
        await update.message.reply_text("⚠️ HOLD UP, SOLDIER! You've hit your daily question limit. Reset at 0600 hours!")
        return ConversationHandler.END
    await update.message.reply_text("🎯 ALRIGHT RECRUIT! What's your career question? Make it count!")
    return ASKING

async def ask_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.chat.id
    question = update.message.text
    await update.message.reply_text("💭 ANALYZING YOUR SITUATION...")
    try:
        answer = await ask_studypal_ai(question)
        await update.message.reply_text(answer)
        log_question(user_id)
    except Exception as e:
        await update.message.reply_text("❌ TECHNICAL DIFFICULTIES, SOLDIER! Regroup and try again!")
        print("LLM error:", e)
    return ConversationHandler.END

async def ask_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("MISSION ABORTED! Ready when you are to try again, soldier! 💪")
    return ConversationHandler.END

def get_ask_handler():
    return ConversationHandler(
        entry_points=[CommandHandler("ask", ask_start)],
        states={
            ASKING: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_receive)],
        },
        fallbacks=[CommandHandler("cancel", ask_cancel)],
    )
