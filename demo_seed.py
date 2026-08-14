"""Idempotent sample data for the public showcase account (see /demo in web_app.py).

Gives a first-time visitor something to look at immediately — an active streak,
a heart balance, a partial cat collection, and a populated leaderboard — without
needing to sign up or wait for a magic-link email.
"""
import random
from datetime import date, timedelta

from db import add_cats, add_hearts, get_collection, get_pg_conn, set_reminders_enabled

DEMO_EMAIL = "demo@mathoclock.app"
DEMO_DISPLAY_NAME = "Demo Cat"
DEMO_GOAL = 5
DEMO_STREAK_DAYS = 12
DEMO_HEARTS = 140
DEMO_CATS = ["proud_check", "celebrating_wave", "victorious_trophy", "curious_eyes", "mathwhiz_glasses"]

# Fake classmates so the leaderboard never looks empty. IDs are well outside
# any real web_users range (which starts at -1 and grows slowly), so they can
# never collide with a real account.
DEMO_CLASSMATES = [
    (-900001, "Mia", 5, 9),
    (-900002, "Jayden", 5, 4),
    (-900003, "Sofia", 6, 15),
]


async def _seed_history(conn, user_id: int, goal: int, days: int) -> int:
    """Backfills daily_track for the past `days` days and returns yesterday's
    done count, so the leaderboard and streak both have something real to show."""
    today = date.today()
    rows = []
    yesterday_done = goal
    for days_ago in range(days, 0, -1):
        day = today - timedelta(days=days_ago)
        done = random.randint(goal, goal + 3)
        if days_ago == 1:
            yesterday_done = done
        rows.append((user_id, day.isoformat(), goal, done))
    await conn.executemany(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, date) DO UPDATE SET goal = EXCLUDED.goal, done = EXCLUDED.done",
        rows,
    )
    return yesterday_done


async def _ensure_confirmed_yesterday(conn, user_id: int, done: int) -> None:
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    await conn.execute(
        "INSERT INTO confirmed_progress (user_id, date, confirmed_done) VALUES ($1, $2, $3) "
        "ON CONFLICT (user_id, date) DO UPDATE SET confirmed_done = EXCLUDED.confirmed_done",
        user_id, yesterday, done,
    )


async def ensure_demo_seeded(user_id: int) -> None:
    conn = await get_pg_conn()
    already_seeded = await conn.fetchval(
        "SELECT 1 FROM daily_track WHERE user_id = $1 LIMIT 1", user_id
    )
    if not already_seeded:
        await conn.execute(
            "UPDATE users SET first_name = $1 WHERE user_id = $2", DEMO_DISPLAY_NAME, user_id
        )
        await _seed_history(conn, user_id, DEMO_GOAL, DEMO_STREAK_DAYS)
        await set_reminders_enabled(user_id, True)
        await add_hearts(user_id, DEMO_HEARTS)

    # Classmates and "yesterday" both need refreshing on every visit, not just
    # the first, since the leaderboard always looks at yesterday's date.
    for classmate_id, name, goal, streak_days in DEMO_CLASSMATES:
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name) VALUES ($1, '', $2) "
            "ON CONFLICT (user_id) DO NOTHING",
            classmate_id, name,
        )
        yesterday_done = await _seed_history(conn, classmate_id, goal, streak_days)
        await _ensure_confirmed_yesterday(conn, classmate_id, yesterday_done)

    demo_yesterday_done = await conn.fetchval(
        "SELECT done FROM daily_track WHERE user_id = $1 AND date = $2",
        user_id, (date.today() - timedelta(days=1)).isoformat(),
    )
    if demo_yesterday_done is not None:
        await _ensure_confirmed_yesterday(conn, user_id, demo_yesterday_done)

    await conn.close()

    # Re-checked every visit (cheap) so a roster swap doesn't leave the demo
    # account holding stickers whose image files no longer exist.
    conn = await get_pg_conn()
    await conn.execute(
        "DELETE FROM cat_collection WHERE user_id = $1 AND cat_type != ALL($2::text[])",
        user_id, DEMO_CATS,
    )
    await conn.close()

    collection = await get_collection(user_id)
    for cat_type in DEMO_CATS:
        if cat_type not in collection:
            await add_cats(user_id, cat_type, 1)
