import asyncio
import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta
from functools import wraps

import requests
from flask import Flask, flash, redirect, render_template, request, session, url_for

from db import (
    add_approved_email,
    add_cats,
    add_hearts,
    clear_buddy_email,
    confirm_buddy,
    consume_gift_token,
    consume_link_code,
    create_link_code,
    get_buddy_status,
    get_buddy_verification,
    get_collection,
    get_confirmed_leaderboard,
    get_confirmed_progress,
    get_gift_token_info,
    get_hearts_balance,
    get_history,
    get_link_status,
    get_or_create_web_user,
    get_pg_conn,
    get_reminders_enabled,
    get_repairable_date,
    get_streak,
    init_db_pg,
    list_approved_emails,
    list_verified_buddies,
    remove_approved_email,
    reset_approved_email_password,
    save_confirmed_progress,
    save_streak_date,
    set_reminders_enabled,
    set_user_password,
    spend_hearts,
    submit_buddy_email,
    transfer_hearts,
    unlock_buddy,
    verify_login_password,
)
from demo_seed import DEMO_EMAIL, ensure_demo_seeded
from flavor import clock_snapshot, compute_badges, daily_quip, heatmap_level, progress_flavor
from mailer import send_buddy_verification

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
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")


def run_async(coro):
    return asyncio.run(coro)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("index"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("is_admin"):
            return redirect(url_for("admin_route"))
        return view(*args, **kwargs)
    return wrapped


# ---- auth ----

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/demo", methods=["POST"])
def demo():
    """Public showcase account — pre-seeded with sample progress so a visitor
    can see how the app works without signing up or waiting on email."""
    user_id = run_async(get_or_create_web_user(DEMO_EMAIL))
    run_async(ensure_demo_seeded(user_id))
    session["user_id"] = user_id
    session["email"] = DEMO_EMAIL
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not EMAIL_RE.match(email):
        flash("That doesn't look like a valid email address.")
        return redirect(url_for("index"))

    if not run_async(verify_login_password(email, password)):
        flash("Wrong email or password.")
        return redirect(url_for("index"))

    user_id = run_async(get_or_create_web_user(email))
    session["user_id"] = user_id
    session["email"] = email
    return redirect(url_for("dashboard"))


# ---- admin ----

@app.route("/admin", methods=["GET"])
def admin_route():
    if not session.get("is_admin"):
        return render_template("admin_login.html")
    emails = run_async(list_approved_emails())
    buddies = run_async(list_verified_buddies())
    return render_template("admin.html", emails=emails, buddies=buddies)


@app.route("/admin/login", methods=["POST"])
def admin_login_route():
    password = request.form.get("password", "")
    if not ADMIN_PASSWORD or password != ADMIN_PASSWORD:
        flash("Wrong password.")
        return redirect(url_for("admin_route"))
    session["is_admin"] = True
    return redirect(url_for("admin_route"))


@app.route("/admin/logout")
def admin_logout_route():
    session.pop("is_admin", None)
    return redirect(url_for("admin_route"))


@app.route("/admin/approved-emails/add", methods=["POST"])
@admin_required
def admin_add_email_route():
    email = request.form.get("email", "").strip().lower()
    if not EMAIL_RE.match(email):
        flash("That doesn't look like a valid email address.")
        return redirect(url_for("admin_route"))
    password = run_async(add_approved_email(email))
    flash(f"Approved {email}. Temporary password: {password} (copy this now, it won't be shown again)")
    return redirect(url_for("admin_route"))


@app.route("/admin/approved-emails/remove", methods=["POST"])
@admin_required
def admin_remove_email_route():
    email = request.form.get("email", "").strip().lower()
    run_async(remove_approved_email(email))
    return redirect(url_for("admin_route"))


@app.route("/admin/approved-emails/reset-password", methods=["POST"])
@admin_required
def admin_reset_password_route():
    email = request.form.get("email", "").strip().lower()
    password = run_async(reset_approved_email_password(email))
    if password:
        flash(f"New temporary password for {email}: {password} (copy this now, it won't be shown again)")
    else:
        flash("That email isn't approved.")
    return redirect(url_for("admin_route"))


@app.route("/admin/buddies/unlock", methods=["POST"])
@admin_required
def admin_unlock_buddy_route():
    user_id = int(request.form.get("user_id"))
    run_async(unlock_buddy(user_id))
    flash("Buddy unlocked. The student can submit a new one.")
    return redirect(url_for("admin_route"))


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


async def _get_day_track(user_id, date_str):
    """Read-only lookup of a specific past date's self-reported numbers —
    unlike _get_today, never creates a row. Used by the buddy confirm step,
    where the date being reviewed isn't necessarily today."""
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT goal, done FROM daily_track WHERE user_id=$1 AND date=$2", user_id, date_str
    )
    await conn.close()
    return (row["goal"], row["done"]) if row else (0, 0)


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
    user_id = session["user_id"]
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    rows = run_async(get_confirmed_leaderboard(yesterday))
    return render_template(
        "leaderboard.html", rows=rows, my_user_id=user_id, board_date=yesterday,
        hearts_balance=run_async(get_hearts_balance(user_id)),
    )


@app.route("/hearts/send", methods=["POST"])
@login_required
def hearts_send_route():
    sender_id = session["user_id"]
    try:
        recipient_id = int(request.form.get("recipient_id", ""))
        amount = int(request.form.get("amount", 0))
    except ValueError:
        flash("That didn't work, try again.")
        return redirect(url_for("leaderboard_route"))

    if amount <= 0:
        flash("Enter a positive number of hearts to send.")
    elif run_async(transfer_hearts(sender_id, recipient_id, amount)):
        flash(f"Sent {amount} heart{'s' if amount != 1 else ''}! ❤️")
    else:
        flash("Couldn't send, check your balance.")
    return redirect(url_for("leaderboard_route"))


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
        hearts_balance=run_async(get_hearts_balance(user_id)),
        repairable_date=run_async(get_repairable_date(user_id)),
        streak_repair_cost=STREAK_REPAIR_COST,
    )


@app.route("/streak/repair", methods=["POST"])
@login_required
def streak_repair_route():
    user_id = session["user_id"]
    repairable_date = run_async(get_repairable_date(user_id))
    if not repairable_date:
        flash("No recent broken day to repair.")
        return redirect(url_for("progress_route"))

    if run_async(spend_hearts(user_id, STREAK_REPAIR_COST)):
        run_async(save_streak_date(user_id, repairable_date))
        flash(f"Streak repaired. {repairable_date} now counts. 💔➡️❤️")
    else:
        flash(f"Not enough hearts, repairing costs {STREAK_REPAIR_COST}.")
    return redirect(url_for("progress_route"))


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
        buddy=run_async(get_buddy_status(user_id)),
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


@app.route("/settings/password", methods=["POST"])
@login_required
def settings_password_route():
    email = session.get("email")
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    if not run_async(verify_login_password(email, current)):
        flash("Current password is wrong.")
    elif len(new) < 6:
        flash("New password needs to be at least 6 characters.")
    elif new != confirm:
        flash("New passwords don't match.")
    else:
        run_async(set_user_password(email, new))
        flash("Password updated.")
    return redirect(url_for("settings_route"))


@app.route("/settings/buddy", methods=["POST"])
@login_required
def settings_buddy_route():
    user_id = session["user_id"]
    email = request.form.get("buddy_email", "").strip()
    own_email = (session.get("email") or "").strip().lower()
    current = run_async(get_buddy_status(user_id))

    if current["status"] == "verified":
        flash("Your buddy is verified and locked. Contact Mathoclock to change it.")
        return redirect(url_for("settings_route"))

    if not email:
        run_async(clear_buddy_email(user_id))
        flash("Accountability buddy removed.")
        return redirect(url_for("settings_route"))

    if not EMAIL_RE.match(email):
        flash("That doesn't look like a valid email address.")
        return redirect(url_for("settings_route"))

    if email.lower() == own_email:
        flash("Your accountability buddy can't be your own email. Pick someone who'll actually check on you.")
        return redirect(url_for("settings_route"))

    token = run_async(submit_buddy_email(user_id, email))
    if not token:
        flash("Your buddy is verified and locked. Contact Mathoclock to change it.")
        return redirect(url_for("settings_route"))

    name = run_async(_get_display_name(user_id))
    confirm_url = f"{PUBLIC_BASE_URL}/buddy/verify/{token}"
    send_buddy_verification(email, name, confirm_url)
    flash(f"Verification email sent to {email}. Nothing counts until they confirm.")
    return redirect(url_for("settings_route"))


@app.route("/buddy/verify/<token>", methods=["GET"])
def buddy_verify_route(token):
    info = run_async(get_buddy_verification(token))
    if not info or info["expired"]:
        return render_template("buddy_verify.html", state="expired")
    if info["confirmed_at"]:
        return render_template("buddy_verify.html", state="already_confirmed")
    name = run_async(_get_display_name(info["user_id"]))
    return render_template("buddy_verify.html", state="pending", student_name=name, token=token)


@app.route("/buddy/verify/<token>", methods=["POST"])
def buddy_verify_submit_route(token):
    note = request.form.get("note", "").strip()
    ok = run_async(confirm_buddy(token, note))
    if not ok:
        return render_template("buddy_verify.html", state="expired")
    return render_template("buddy_verify.html", state="confirmed")


# ---- Pomewdoro: focus timer that drops a collectible cat every completed minute ----

FOCUS_MINUTES = 25
BREAK_MINUTES = 5
BURST_PARTICLE_COUNT = 18  # must match pomewdoro.js — sanity bound on hearts_caught
STREAK_REPAIR_COST = 50

# The 12-cat collectible roster for Pomewdoro drops — distinct from the 5 mood
# sprites used on the dashboard, which stay tied to actual progress state.
CAT_ROSTER = {
    "proud_check": "Proud",
    "confident_seal": "Confident",
    "determined_frown": "Determined",
    "determined_pencil": "Focused",
    "celebrating_burst": "Celebration",
    "celebrating_sparkle": "Sparkle",
    "celebrating_wave": "High Five",
    "victorious_trophy": "Victorious",
    "flowing_swirl": "In The Zone",
    "leveledup_hat1": "Leveled Up",
    "leveledup_hat2": "Graduate",
    "sleepy_curl": "Sleepy",
    "working_late1": "Night Owl",
    "working_late2": "Midnight Oil",
    "yawning_bubble": "Yawning",
    "cozy_reading1": "Cozy Reader",
    "cozy_reading2": "Bookworm",
    "stargazing_night": "Stargazer",
    "curious_eyes": "Curious",
    "silly_xeyes": "Silly",
    "silly_googly": "Goofy",
    "playful_pounce": "Playful",
    "excited_spin1": "Excited",
    "excited_spin2": "Spinning With Joy",
    "mathwhiz_glasses": "Math Whiz",
    "numberobsessed_spiral": "Number Obsessed",
    "sleepycloud1": "Dreamy",
    "sleepycloud2": "Cloud Nine",
}
CAT_TYPES = list(CAT_ROSTER.keys())

# Placeholder for the future paid tier — no payment processing wired up yet, so
# these are inert/disabled on the gift-picker page rather than purchasable.
PREMIUM_CATALOG = {
    "premium_star": "Golden Star",
}


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
    """Clears session state for a completed/interrupted focus or break phase, and
    auto-starts a break only if the focus block ran to full completion. Cats are no
    longer earned here — they're gifted by an accountability buddy via the nightly
    report instead (see gift_tokens)."""
    state = _pomo_state()
    if not state:
        return None

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

    return {"phase": phase, "started_break": started_break, "completed_fully": completed_fully}


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
        state=state, focus_minutes=FOCUS_MINUTES, break_minutes=BREAK_MINUTES,
    )


@app.route("/pomewdoro/start", methods=["POST"])
@login_required
def pomewdoro_start_route():
    session["pomo_phase"] = "focus"
    session["pomo_start"] = datetime.utcnow().isoformat()
    session["pomo_duration"] = FOCUS_MINUTES
    return redirect(url_for("pomewdoro_route"))


@app.route("/pomewdoro/pause-adjust", methods=["POST"])
@login_required
def pomewdoro_pause_adjust_route():
    """Called when the tab regains focus after being hidden. Pushes pomo_start
    forward by the reported away-duration, so the server's own elapsed-time
    calculation — the one actually used to bank cats/hearts and detect
    completion — genuinely excludes time spent off the tab, not just the
    on-screen countdown. Leaving the tab open in the background no longer
    counts as focus time."""
    start_iso = session.get("pomo_start")
    if not start_iso:
        return ("", 204)
    try:
        paused_seconds = float(request.form.get("paused_seconds", 0))
    except ValueError:
        paused_seconds = 0

    start = datetime.fromisoformat(start_iso)
    # never push pomo_start past "now" — bound by real elapsed time, not just an
    # arbitrary ceiling, so a bad/garbage report can't send elapsed_seconds negative
    real_elapsed = max(0.0, (datetime.utcnow() - start).total_seconds())
    paused_seconds = max(0.0, min(paused_seconds, real_elapsed, 3600.0))

    if paused_seconds > 0:
        session["pomo_start"] = (start + timedelta(seconds=paused_seconds)).isoformat()
    return ("", 204)


@app.route("/pomewdoro/finish", methods=["POST"])
@login_required
def pomewdoro_finish_route():
    user_id = session["user_id"]
    result = _finish_pomo(user_id)

    # hearts_caught is only meaningful — and only ever sent by the client — when a
    # full focus session just completed and the burst actually fired. Anything else
    # (early stop, ending a break) gets the claim ignored regardless of what's posted.
    if result and result["started_break"]:
        try:
            hearts_caught = int(request.form.get("hearts_caught", 0))
        except ValueError:
            hearts_caught = 0
        hearts_caught = max(0, min(hearts_caught, BURST_PARTICLE_COUNT))
        if hearts_caught:
            run_async(add_hearts(user_id, hearts_caught))
            flash(f"Caught {hearts_caught} heart{'s' if hearts_caught != 1 else ''}! ❤️")

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


# ---- gifting: public, no-login pages a buddy reaches from the nightly report ----

@app.route("/gift/<token>")
def gift_picker_route(token):
    info = run_async(get_gift_token_info(token))
    if info is None:
        return render_template("gift_invalid.html"), 404
    recipient_id, report_date = info["user_id"], info["report_date"]
    name = run_async(_get_display_name(recipient_id))

    confirmed = run_async(get_confirmed_progress(recipient_id, report_date)) if report_date else None
    if confirmed is None:
        # step 1: must confirm the real count before a sticker can be picked
        _, reported_done = run_async(_get_day_track(recipient_id, report_date)) if report_date else (0, 0)
        return render_template(
            "gift_confirm.html", token=token, name=name, reported_done=reported_done, report_date=report_date
        )

    # step 2: already confirmed — show the sticker picker
    return render_template(
        "gift_picker.html", token=token, name=name, confirmed_done=confirmed,
        roster=CAT_ROSTER, premium=PREMIUM_CATALOG,
    )


@app.route("/gift/<token>/confirm", methods=["POST"])
def gift_confirm_route(token):
    info = run_async(get_gift_token_info(token))
    if info is None:
        return render_template("gift_invalid.html"), 404
    recipient_id, report_date = info["user_id"], info["report_date"]
    if not report_date:
        return render_template("gift_invalid.html"), 404

    try:
        confirmed_done = max(0, int(request.form.get("confirmed_done", 0)))
    except ValueError:
        flash("Enter a valid number.")
        return redirect(url_for("gift_picker_route", token=token))

    run_async(save_confirmed_progress(recipient_id, report_date, confirmed_done))
    return redirect(url_for("gift_picker_route", token=token))


@app.route("/gift/<token>/award", methods=["POST"])
def gift_award_route(token):
    cat_type = request.form.get("cat_type", "")
    if cat_type not in CAT_ROSTER:
        flash("That sticker isn't available.")
        return redirect(url_for("gift_picker_route", token=token))

    recipient_id = run_async(consume_gift_token(token))
    if recipient_id is None:
        return render_template("gift_invalid.html"), 404

    run_async(add_cats(recipient_id, cat_type, 1))
    name = run_async(_get_display_name(recipient_id))
    return render_template("gift_sent.html", name=name, cat_name=CAT_ROSTER[cat_type])


# Runs on import so schema creation happens under gunicorn too, not just `python web_app.py`.
run_async(init_db_pg())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=bool(os.getenv("FLASK_DEBUG")))
