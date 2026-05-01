import os
import asyncio
import random
from datetime import date
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application
from db import get_pg_conn, get_user_profiles, save_wrapup_log
import httpx
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# --- Configuration ---
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
GIPHY_KEY = os.getenv("GIPHY_API_KEY", "")
GIPHY_ENDPOINT = "https://api.giphy.com/v1/gifs/random"

# --- Helper Functions ---
async def fetch_leaderboard_positions() -> tuple[tuple[int, int], tuple[int, int]]:
    conn = await get_pg_conn()
    today = date.today().isoformat()
    rows = await conn.fetch(
        "SELECT user_id, done FROM daily_track WHERE date = $1 ORDER BY done DESC",
        today
    )
    await conn.close()
    if not rows:
        return (None, 0), (None, 0)
    return (rows[0]['user_id'], rows[0]['done']), (rows[-1]['user_id'], rows[-1]['done'])

async def get_cat_gif_url() -> str:
    if not GIPHY_KEY:
        return "https://media.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif"
    params = {"api_key": GIPHY_KEY, "tag": "space cat", "rating": "G"}
    try:
        resp = httpx.get(GIPHY_ENDPOINT, params=params, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("images", {}).get("original", {}).get("url", "")
    except Exception as e:
        logger.error(f"Giphy API error: {e}")
        return "https://media.giphy.com/media/JIX9t2j0ZTN9S/giphy.gif"

async def call_openrouter(prompt: str) -> str:
    if not OPENROUTER_KEY:
        raise RuntimeError("No OpenRouter API key configured.")
    headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "system", "content": "You are a cold, elite study coach with a sharp tongue and high standards. Keep it short, human, no fluff."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 400
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(OPENROUTER_ENDPOINT, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data['choices'][0]['message']['content'].strip()

async def call_ollama(prompt: str) -> str:
    async with httpx.AsyncClient() as client:
        payload = {"model": OLLAMA_MODEL, "prompt": prompt, "max_tokens": 300}
        url = f"{OLLAMA_URL}/v1/chat/completions"
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        return data.get('choices', [])[0].get('message', {}).get('content', '').strip()

# --- Group Wrap-Up Message ---
async def build_wrapup_message(top, least, chat_names, user_profiles) -> str:
    top_id, top_count = top
    least_id, least_count = least
    top_name = chat_names.get(top_id, str(top_id))
    least_name = chat_names.get(least_id, str(least_id))
    top_goal = user_profiles.get(top_id, {}).get("goal", 10)
    least_goal = user_profiles.get(least_id, {}).get("goal", 10)
    top_trait = user_profiles.get(top_id, {}).get("trait", "focused")
    least_trait = user_profiles.get(least_id, {}).get("trait", "casual")

    prompt = f"""Generate a short, 7-line daily leaderboard wrap-up.
Tone: cold, direct, tactical. No fluff. Tips should be concrete and realistic.

1. Start with: 'Ladies and gentlemen,'
2. Celebrate top performer: {top_name} nailed {top_count}/{top_goal}. Trait: {top_trait}. Compliment them.
3. Roast lowest performer: {least_name} did {least_count}/{least_goal}. Trait: {least_trait}. Light roast, keep it clever.
4. For users who completed 0–33%: give 1 clear, tactical study tip (e.g., best times to study, focus techniques).
5. For users at 34–67%: give 1 time-management or focus-related tip they can act on immediately.
6. For users at 67–100%: give 1 advanced strategy (e.g., spaced repetition, group study) to improve quality.
7. End with: 'One percent better tomorrow.'
"""

    try:
        if OPENROUTER_KEY:
            logger.info("Trying OpenRouter for wrapup message...")
            return await call_openrouter(prompt)
        elif OLLAMA_MODEL:
            logger.info("Trying Ollama for wrapup message...")
            return await call_ollama(prompt)
    except Exception as e:
        logger.error(f"LLM error: {e}")

    return (
        f"Ladies and gentlemen,\n"
        f"{top_name} crushed it with {top_count}/{top_goal}. You’re a beast.\n"
        f"{least_name} logged {least_count}/{least_goal}. Even your inbox feels sorry.\n"
        "\ud83d\udd34 0–33%: Apply before 11AM and avoid multitasking. Use a timer.\n"
        "\ud83d\udd36 34–67%: Write down tomorrow’s goal before bed. Prime your brain.\n"
        "\ud83d\udd35 67–100%: Message one hiring manager today with a tailored note.\n"
        "One percent better tomorrow."
    )

# --- Main Scheduler Function ---
async def send_wrapup(application: Application, chat_ids: list[int], chat_names: dict[int, str], user_profiles: dict[int, dict]):
    top, least = await fetch_leaderboard_positions()
    group_msg = await build_wrapup_message(top, least, chat_names, user_profiles)
    gif_url = await get_cat_gif_url()

    # Group leaderboard wrap-up
    for chat_id in chat_ids:
        await application.bot.send_message(chat_id=chat_id, text=group_msg)
        await application.bot.send_animation(chat_id=chat_id, animation=gif_url)

    await save_wrapup_log(group_msg, date.today(), user_id=None)

    # Personalized nudges
    for chat_id in chat_ids:
        profile = user_profiles.get(chat_id, {})
        name = chat_names.get(chat_id, str(chat_id))
        done = profile.get("done", 0)
        goal = profile.get("goal", 10)
        streak = profile.get("streak", 0)
        trait = profile.get("trait", "ambiguous mystery")
        percent = int((done / goal) * 100) if goal else 0

        prompt = f"""Create a 4-line cold motivational message for a student:
Name: {name}
Done: {done}/{goal} ({percent}%)
Streak: {streak} days
Personality: {trait}

Tone: cold, elite coach. Include one sharp tip. End with 'One percent better tomorrow.'
"""

        try:
            message = await call_openrouter(prompt)
        except Exception as e:
            logger.warning(f"Fallback message for {name} due to LLM error: {e}")
            message = f"{name}, you did {done}/{goal}. Get back in gear tomorrow.\nOne percent better tomorrow."

        gif = await get_cat_gif_url()
        await application.bot.send_message(chat_id=chat_id, text=message)
        await application.bot.send_animation(chat_id=chat_id, animation=gif)

        await save_wrapup_log(message, date.today(), user_id=chat_id)

# Optional: test manually
if __name__ == "__main__":
    from telegram.ext import ApplicationBuilder
    from config import TELEGRAM_BOT_TOKEN

    async def run():
        app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
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

    asyncio.run(run())
