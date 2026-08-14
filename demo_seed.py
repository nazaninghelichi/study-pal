"""Idempotent sample data for the public showcase account (see /demo in web_app.py).

Gives a first-time visitor something to look at immediately — an active streak,
a heart balance, and a partial cat collection — without needing to sign up or
wait for a magic-link email.
"""
import random
from datetime import date, timedelta

from db import add_cats, add_hearts, get_pg_conn, set_reminders_enabled

DEMO_EMAIL = "demo@mathoclock.app"
DEMO_DISPLAY_NAME = "Demo Cat"
DEMO_GOAL = 5
DEMO_STREAK_DAYS = 12
DEMO_HEARTS = 140
DEMO_CATS = ["leo", "nyx", "misty", "rusty", "duchess"]


async def ensure_demo_seeded(user_id: int) -> None:
    conn = await get_pg_conn()
    already_seeded = await conn.fetchval(
        "SELECT 1 FROM daily_track WHERE user_id = $1 LIMIT 1", user_id
    )
    if already_seeded:
        await conn.close()
        return

    await conn.execute(
        "UPDATE users SET first_name = $1 WHERE user_id = $2", DEMO_DISPLAY_NAME, user_id
    )

    today = date.today()
    rows = []
    for days_ago in range(DEMO_STREAK_DAYS, 0, -1):
        day = today - timedelta(days=days_ago)
        done = random.randint(DEMO_GOAL, DEMO_GOAL + 3)
        rows.append((user_id, day.isoformat(), DEMO_GOAL, done))
    await conn.executemany(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, date) DO UPDATE SET goal = EXCLUDED.goal, done = EXCLUDED.done",
        rows,
    )
    await conn.close()

    await set_reminders_enabled(user_id, True)
    await add_hearts(user_id, DEMO_HEARTS)
    for cat_type in DEMO_CATS:
        await add_cats(user_id, cat_type, 1)
