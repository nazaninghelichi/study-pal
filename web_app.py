import asyncio
import logging
import os
import re
import secrets
from datetime import date, timedelta
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for

from db import (
    consume_magic_link,
    create_magic_link,
    get_or_create_web_user,
    get_pg_conn,
    init_db_pg,
)
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

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


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


async def _get_leaderboard():
    today = date.today().isoformat()
    conn = await get_pg_conn()
    rows = await conn.fetch(
        """
        SELECT u.user_id, COALESCE(NULLIF(u.username, ''), u.first_name) AS display_name, dt.done
        FROM daily_track dt JOIN users u ON u.user_id = dt.user_id
        WHERE dt.date = $1 AND dt.done > 0
        ORDER BY dt.done DESC LIMIT 5
        """,
        today
    )
    await conn.close()
    return [dict(r) for r in rows]


# ---- app routes ----

@app.route("/dashboard")
@login_required
def dashboard():
    user_id = session["user_id"]
    goal, done = run_async(_get_today(user_id))
    name = run_async(_get_display_name(user_id))
    return render_template("dashboard.html", goal=goal, done=done, name=name)


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
    rows = run_async(_get_leaderboard())
    return render_template("leaderboard.html", rows=rows, my_user_id=session["user_id"])


@app.route("/progress")
@login_required
def progress_route():
    days = run_async(_get_week(session["user_id"]))
    total_goal = sum(d["goal"] for d in days)
    total_done = sum(d["done"] for d in days)
    pct = round(total_done / total_goal * 100, 1) if total_goal else 0
    return render_template("progress.html", days=days, total_goal=total_goal, total_done=total_done, pct=pct)


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
    return render_template("settings.html", name=name, email=session.get("email"))


# Runs on import so schema creation happens under gunicorn too, not just `python web_app.py`.
run_async(init_db_pg())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=bool(os.getenv("FLASK_DEBUG")))
