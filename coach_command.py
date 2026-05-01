from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import CommandHandler, CallbackContext, CallbackQueryHandler
from datetime import datetime, timedelta
import sqlite3
import requests
from config import OPENROUTER_API_KEY

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

def coachsummary(update: Update, context: CallbackContext):
    user_id = update.effective_chat.id
    today = datetime.now()
    start_of_week = today - timedelta(days=today.weekday())
    this_week = [start_of_week + timedelta(days=i) for i in range(7)]
    weekday_map = {d.strftime("%A"): d.strftime("%Y-%m-%d") for d in this_week}

    try:
        conn = sqlite3.connect("studypal.db")
        c = conn.cursor()
        c.execute("SELECT weekday, goal_count FROM user_goals WHERE user_id = ?", (user_id,))
        goals = dict(c.fetchall())

        applied = {}
        for weekday, date_str in weekday_map.items():
            c.execute("SELECT count_applied FROM user_progress WHERE user_id = ? AND date = ?", (user_id, date_str))
            row = c.fetchone()
            applied[weekday] = row[0] if row else 0

    except Exception as e:
        print("Database error:", e)
        update.message.reply_text("❌ Couldn't load your weekly data.")
        return
    finally:
        conn.close()

    total_goal = sum(goals.get(day, 0) for day in WEEKDAYS)
    total_done = sum(applied.get(day, 0) for day in WEEKDAYS)
    percent = round((total_done / total_goal * 100), 1) if total_goal else 0

    stats_text = "\n".join([
        f"{day}: Goal {goals.get(day, 0)} | Applied {applied.get(day, 0)}"
        for day in WEEKDAYS
    ])

    coach_prompt = f"""
You are a cold and realistic study coach.
The user has job search goals and progress stats.
Give blunt feedback and estimate how long it may take to land interviews at this rate.
No fluff. Speak like a coach who cares more about results than feelings.

Weekly Summary:
Total Goal: {total_goal}
Total Applied: {total_done}
Completion Rate: {percent}%

Day-by-day breakdown:
{stats_text}
"""

    llm_message = get_llm_feedback(coach_prompt)

    update.message.reply_text(
        f"⚠️ *Coach Mode Activated*\n"
        f"This is blunt feedback meant to push you.\n\n"
        f"📊 Weekly Total: {total_done}/{total_goal} ({percent}%)\n\n"
        f"🧠 Coach says:\n{llm_message}",
        parse_mode="Markdown"
    )

def get_llm_feedback(prompt: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "mistralai/mistral-7b-instruct",
        "messages": [
            {"role": "system", "content": "You are a tough love study coach."},
            {"role": "user", "content": prompt.strip()}
        ]
    }
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print("Coach LLM error:", e)
        return "(⚠️ Error fetching coach feedback.)"

def ask_weekly_summary(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("✅ Regular Summary", callback_data="summary_choice")],
        [InlineKeyboardButton("🧠 Coach Summary (tough love)", callback_data="coach_choice")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("How would you like your weekly summary?", reply_markup=reply_markup)

def handle_summary_choice(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    choice = query.data

    # Use original message’s chat to get message object safely
    message: Message = query.message

    # Create a dummy Update for compatibility
    dummy_update = Update(update.update_id, message=message)

    if choice == "summary_choice":
        from goal_command import summary
        summary(dummy_update, context)
    elif choice == "coach_choice":
        coachsummary(dummy_update, context)

def get_coachsummary_handler():
    return [
        CommandHandler("coachsummary", coachsummary),
        CommandHandler("weeklyreview", ask_weekly_summary),
        CallbackQueryHandler(handle_summary_choice, pattern="^(summary_choice|coach_choice)$")
    ]
