import asyncio
import logging
import os
import random
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for

from db import (
    add_cats,
    consume_link_code,
    consume_magic_link,
    create_link_code,
    create_magic_link,
    get_collection,
    get_full_leaderboard,
    get_history,
    get_link_status,
    get_or_create_web_user,
    get_pg_conn,
    get_reminders_enabled,
    get_streak,
    init_db_pg,
    set_reminders_enabled,
)
from flavor import clock_snapshot, compute_badges, daily_quip, heatmap_level, progress_flavor
from mailer import send_magic_link

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    logger.warning("SECRET_KEY not set — using an ephemeral key; sessions won't survive a restart.")
    SECRET_KEY = secrets.token_hex(32)
app.secret_key = SECRET_KEY

PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:5000").rstrip("/")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def run_async(coro):
    return asyncio.run(coro)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


# ---- auth ----

def _dev_mode() -> bool:
    """True when no real SMTP is configured — gates the demo-login shortcut
    so it can never be used to skip auth once real email is wired up."""
    return not bool(os.getenv("SMTP_HOST"))


@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html", dev_mode=_dev_mode())


@app.route("/demo-login", methods=["POST"])
def demo_login():
    if not _dev_mode():
        flash("Demo login is disabled once real email is configured.")
        return redirect(url_for("index"))
    email = "demo@studypal.local"
    user_id = run_async(get_or_create_web_user(email))
    session["user_id"] = user_id
    session["email"] = email
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    if not EMAIL_RE.match(email):
        flash("That doesn't look like a valid email address.")
        return redirect(url_for("index"))

    token = run_async(create_magic_link(email))
    link = f"{PUBLIC_BASE_URL}/auth/verify?token={token}"
    send_magic_link(email, link)

    dev_link = link if not os.getenv("SMTP_HOST") else None
    return render_template("check_email.html", email=email, dev_link=dev_link)


@app.route("/auth/verify")
def verify():
    token = request.args.get("token", "")
    email = run_async(consume_magic_link(token))
    if not email:
        flash("That link is invalid or has expired — request a new one.")
        return redirect(url_for("index"))

    user_id = run_async(get_or_create_web_user(email))
    session["user_id"] = user_id
    session["email"] = email
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/about")
def about_route():
    return render_template("about.html")


# ---- data helpers (mirror the Telegram bot's goal_command.py / leaderboard_command.py logic) ----

async def _get_today(user_id):
    today = date.today().isoformat()
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT goal, done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, today
    )
    if row:
        await conn.close()
        return row["goal"], row["done"]
    last = await conn.fetchrow(
        "SELECT goal FROM daily_track WHERE user_id=$1 ORDER BY date DESC LIMIT 1", user_id
    )
    goal = last["goal"] if last else 0
    await conn.execute(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, 0)",
        user_id, today, goal
    )
    await conn.close()
    return goal, 0


async def _set_goal(user_id, new_goal):
    today = date.today().isoformat()
    conn = await get_pg_conn()
    done_row = await conn.fetchrow(
        "SELECT done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, today
    )
    done = done_row["done"] if done_row else 0
    await conn.execute(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, date) DO UPDATE SET goal = EXCLUDED.goal",
        user_id, today, new_goal, done
    )
    await conn.close()


async def _log_progress(user_id, delta):
    today = date.today().isoformat()
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT goal, done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, today
    )
    goal = row["goal"] if row else 0
    done = max(0, (row["done"] if row else 0) + delta)
    await conn.execute(
        "INSERT INTO daily_track (user_id, date, goal, done) VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (user_id, date) DO UPDATE SET done = EXCLUDED.done",
        user_id, today, goal, done
    )
    await conn.close()


async def _get_display_name(user_id):
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT COALESCE(NULLIF(username, ''), first_name) AS display_name FROM users WHERE user_id=$1",
        user_id
    )
    await conn.close()
    return row["display_name"] if row and row["display_name"] else "there"


async def _set_display_name(user_id, name):
    conn = await get_pg_conn()
    await conn.execute("UPDATE users SET username=$1 WHERE user_id=$2", name, user_id)
    await conn.close()


async def _get_week(user_id):
    today_date = date.today()
    start_week = today_date - timedelta(days=today_date.weekday())
    conn = await get_pg_conn()
    days = []
    for i in range(7):
        d = start_week + timedelta(days=i)
        row = await conn.fetchrow(
            "SELECT goal, done FROM daily_track WHERE user_id=$1 AND date=$2",
            user_id, d.isoformat()
        )
        g, dn = (row["goal"], row["done"]) if row else (0, 0)
        days.append({
            "label": d.strftime("%a"),
            "goal": g,
            "done": dn,
            "is_today": d == today_date,
        })
    await conn.close()
    return days


def fetch_cat_gif() -> str | None:
    """Read GIPHY_API_KEY straight from the env — not from config.py, whose
    top-level `os.environ["DATABASE_URL"]` would crash this whole app on
    import if only RAILWAY_DATABASE_URL were set."""
    api_key = os.getenv("GIPHY_API_KEY")
    if not api_key:
        return None
    try:
        resp = requests.get(
            "https://api.giphy.com/v1/gifs/random",
            params={"api_key": api_key, "tag": "math cat", "rating": "g"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("images", {}).get("original", {}).get("url")
    except Exception as e:
        logger.warning("Giphy fetch failed: %s", e)
        return None


def get_reward_gif(done: int, goal: int) -> str | None:
    """Cache one gif per day in the session so hitting refresh doesn't burn API calls."""
    if goal <= 0 or done < goal:
        return None
    cache_key = f"gif_{date.today().isoformat()}"
    if session.get(cache_key):
        return session[cache_key]
    gif_url = fetch_cat_gif()
    if gif_url:
        session[cache_key] = gif_url
    return gif_url


# ---- app routes ----

@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    goal, done = run_async(_get_today(user_id))
    name = run_async(_get_display_name(user_id))
    return render_template(
        "dashboard.html",
        goal=goal,
        done=done,
        name=name,
        flavor=progress_flavor(done, goal),
        quip=daily_quip(),
        gif_url=get_reward_gif(done, goal),
        clock=clock_snapshot(),
    )


@app.route("/goal", methods=["POST"])
@login_required
def set_goal_route():
    try:
        new_goal = max(0, int(request.form.get("goal", 0)))
    except ValueError:
        new_goal = 0
    run_async(_set_goal(session["user_id"], new_goal))
    return redirect(url_for("dashboard"))


@app.route("/log", methods=["POST"])
@login_required
def log_route():
    delta = 1 if request.form.get("action") == "inc" else -1
    run_async(_log_progress(session["user_id"], delta))
    return redirect(url_for("dashboard"))


@app.route("/leaderboard")
@login_required
def leaderboard_route():
    rows = run_async(get_full_leaderboard())
    return render_template("leaderboard.html", rows=rows, my_user_id=session["user_id"])


def _build_heatmap(history):
    """Shapes a flat oldest-first day list into GitHub-style week columns with month labels."""
    if not history:
        return {"weeks": [], "months": []}

    start_date = history[0]["date"]
    lead_padding = (start_date.weekday() + 1) % 7  # weekday(): Mon=0..Sun=6 -> we want Sun-first columns
    cells = [None] * lead_padding + [
        {"date": h["date"], "level": heatmap_level(h["done"], h["goal"])} for h in history
    ]
    trail_padding = (7 - len(cells) % 7) % 7
    cells += [None] * trail_padding
    weeks = [cells[i:i + 7] for i in range(0, len(cells), 7)]

    months = []
    last_month = None
    for week in weeks:
        label = ""
        for cell in week:
            if cell and cell["date"].day <= 7 and cell["date"].month != last_month:
                label = cell["date"].strftime("%b")
                last_month = cell["date"].month
                break
        months.append(label)
    return {"weeks": weeks, "months": months}


@app.route("/progress")
@login_required
def progress_route():
    user_id = session["user_id"]
    days = run_async(_get_week(user_id))
    total_goal = sum(d["goal"] for d in days)
    total_done = sum(d["done"] for d in days)
    pct = round(total_done / total_goal * 100, 1) if total_goal else 0

    history = run_async(get_history(user_id))
    streak = run_async(get_streak(user_id))

    return render_template(
        "progress.html",
        days=days, total_goal=total_goal, total_done=total_done, pct=pct,
        streak=streak,
        badges=compute_badges(history),
        heatmap=_build_heatmap(history),
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_route():
    user_id = session["user_id"]
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if name:
            run_async(_set_display_name(user_id, name))
            flash("Display name updated.")
        return redirect(url_for("settings_route"))

    name = run_async(_get_display_name(user_id))
    return render_template(
        "settings.html",
        name=name,
        email=session.get("email"),
        link_status=run_async(get_link_status(user_id)),
        reminders_enabled=run_async(get_reminders_enabled(user_id)),
        link_code=session.pop("pending_link_code", None),
    )


@app.route("/settings/link", methods=["POST"])
@login_required
def settings_link_route():
    session["pending_link_code"] = run_async(create_link_code(session["user_id"]))
    return redirect(url_for("settings_route"))


@app.route("/settings/notifications", methods=["POST"])
@login_required
def settings_notifications_route():
    run_async(set_reminders_enabled(session["user_id"], request.form.get("enabled") == "1"))
    return redirect(url_for("settings_route"))


# ---- Pomewdoro: focus timer that drops a collectible cat every completed minute ----

FOCUS_MINUTES = 25
BREAK_MINUTES = 5

# The 12-cat collectible roster for Pomewdoro drops — distinct from the 5 mood
# sprites used on the dashboard, which stay tied to actual progress state.
CAT_ROSTER = {
    "leo": "Leo",
    "nyx": "Nyx",
    "misty": "Misty",
    "rusty": "Rusty",
    "patch": "Patch",
    "sly": "Sly",
    "duchess": "Duchess",
    "luna": "Luna",
    "cubery": "Cubery",
    "boots1": "Boots",
    "boots2": "Boots",
    "ollie": "Ollie",
}
CAT_TYPES = list(CAT_ROSTER.keys())


def _pomo_state():
    """Reads the active phase from the session and computes real elapsed/remaining time
    server-side — the client's countdown is just for display, never trusted for banking cats."""
    phase = session.get("pomo_phase")
    start_iso = session.get("pomo_start")
    duration = session.get("pomo_duration")
    if not phase or not start_iso:
        return None
    start = datetime.fromisoformat(start_iso)
    elapsed = (datetime.utcnow() - start).total_seconds()
    return {
        "phase": phase,
        "duration_minutes": duration,
        "elapsed_seconds": min(elapsed, duration * 60),
        "remaining_seconds": max(0, duration * 60 - elapsed),
    }


def _finish_pomo(user_id):
    """Banks cats for a completed/interrupted focus phase, clears session state, and
    auto-starts a break only if the focus block ran to full completion."""
    state = _pomo_state()
    if not state:
        return None

    earned = {}
    if state["phase"] == "focus":
        elapsed_minutes = int(state["elapsed_seconds"] // 60)
        for _ in range(elapsed_minutes):
            cat_type = random.choice(CAT_TYPES)
            earned[cat_type] = earned.get(cat_type, 0) + 1
        for cat_type, n in earned.items():
            run_async(add_cats(user_id, cat_type, n))

    completed_fully = state["elapsed_seconds"] >= state["duration_minutes"] * 60 - 1
    phase = state["phase"]

    session.pop("pomo_phase", None)
    session.pop("pomo_start", None)
    session.pop("pomo_duration", None)

    started_break = phase == "focus" and completed_fully
    if started_break:
        session["pomo_phase"] = "break"
        session["pomo_start"] = datetime.utcnow().isoformat()
        session["pomo_duration"] = BREAK_MINUTES

    return {"phase": phase, "earned": earned, "started_break": started_break}


@app.route("/pomewdoro")
@login_required
def pomewdoro_route():
    user_id = session["user_id"]
    state = _pomo_state()
    if state and state["remaining_seconds"] <= 0:
        _finish_pomo(user_id)
        state = _pomo_state()
    return render_template(
        "pomewdoro.html",
        state=state, focus_minutes=FOCUS_MINUTES, break_minutes=BREAK_MINUTES, cat_types=CAT_TYPES,
    )


@app.route("/pomewdoro/start", methods=["POST"])
@login_required
def pomewdoro_start_route():
    session["pomo_phase"] = "focus"
    session["pomo_start"] = datetime.utcnow().isoformat()
    session["pomo_duration"] = FOCUS_MINUTES
    return redirect(url_for("pomewdoro_route"))


@app.route("/pomewdoro/finish", methods=["POST"])
@login_required
def pomewdoro_finish_route():
    result = _finish_pomo(session["user_id"])
    if result and result["earned"]:
        total = sum(result["earned"].values())
        flash(f"Collected {total} cat{'s' if total != 1 else ''}! 🐱")
    return redirect(url_for("pomewdoro_route"))


@app.route("/collection")
@login_required
def collection_route():
    raw_counts = run_async(get_collection(session["user_id"]))
    # only count against the current roster — a user_id can carry orphaned
    # cat_type rows from an earlier roster (e.g. the old mood-sprite set)
    counts = {slug: raw_counts[slug] for slug in CAT_ROSTER if raw_counts.get(slug)}
    found = len(counts)
    return render_template(
        "collection.html", counts=counts, found=found, total=sum(counts.values()), roster=CAT_ROSTER
    )


# Runs on import so schema creation happens under gunicorn too, not just `python web_app.py`.
run_async(init_db_pg())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=bool(os.getenv("FLASK_DEBUG")))
