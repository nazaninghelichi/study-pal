import os
import secrets
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
import asyncpg

# Load .env for local development (dotenv is optional in production)
load_dotenv()

# Build DATABASE_URL with priority:
# 1. RAILWAY_DATABASE_URL (injected by Railway)
# 2. DATABASE_URL (standard)
# 3. DEV_DATABASE_URL (local development)
# 4. Individual PG* vars (PGHOST, PGPORT, PGUSER, PGPASSWORD, PGDATABASE)
# 5. Fallback to localhost default
railway_url = os.getenv("RAILWAY_DATABASE_URL", "").strip()
prod_url    = os.getenv("DATABASE_URL", "").strip()
dev_url     = os.getenv("DEV_DATABASE_URL", "").strip()
pg_host     = os.getenv("PGHOST")
pg_port     = os.getenv("PGPORT")
pg_user     = os.getenv("PGUSER")
pg_password = os.getenv("PGPASSWORD")
pg_database = os.getenv("PGDATABASE")

if railway_url:
    DATABASE_URL = railway_url
elif prod_url:
    DATABASE_URL = prod_url
elif dev_url:
    DATABASE_URL = dev_url
elif pg_host and pg_port and pg_user and pg_password and pg_database:
    DATABASE_URL = (
        f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"
    )
else:
    # Last-resort localhost fallback
    DATABASE_URL = "postgresql://postgres:secret@localhost:5432/railway"

async def get_pg_conn():
    """
    Return a new asyncpg connection to the Postgres database.
    """
    return await asyncpg.connect(DATABASE_URL)

# Alias for backward compatibility
get_db_connection = get_pg_conn

async def init_db_pg():
    """
    Initialize the Postgres schema: users, daily_track, user_preferences, and wrapup_logs tables.
    """
    conn = await get_pg_conn()

    # Create users table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
          user_id    BIGINT PRIMARY KEY,
          username   TEXT,
          first_name TEXT
        );
        """
    )

    # Create daily_track table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_track (
          user_id BIGINT,
          date    TEXT,
          goal    INTEGER DEFAULT 0,
          done    INTEGER DEFAULT 0,
          PRIMARY KEY(user_id, date)
        );
        """
    )

    # Create user_preferences table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_preferences (
          user_id           BIGINT PRIMARY KEY,
          reminders_enabled BOOLEAN    DEFAULT TRUE
        );
        """
    )

    # Create wrapup_logs table
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wrapup_logs (
          id         SERIAL PRIMARY KEY,
          date       DATE NOT NULL,
          user_id    BIGINT,
          content    TEXT NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Create web_users table (email-login users, distinct from Telegram users)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS web_users (
          id         SERIAL PRIMARY KEY,
          email      TEXT UNIQUE NOT NULL,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Create approved_emails table (allowlist gating who can request a sign-in link)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS approved_emails (
          email      TEXT PRIMARY KEY,
          added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # Create magic_links table (single-use, expiring email sign-in tokens)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS magic_links (
          token      TEXT PRIMARY KEY,
          email      TEXT NOT NULL,
          expires_at TIMESTAMP NOT NULL,
          used       BOOLEAN DEFAULT FALSE,
          created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # account_links: ties one web_user_id to one telegram_user_id (1:1 both directions)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_links (
          web_user_id      BIGINT PRIMARY KEY,
          telegram_user_id BIGINT UNIQUE NOT NULL,
          linked_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # link_codes: single-use, expiring codes the website issues and the bot consumes
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS link_codes (
          code        TEXT PRIMARY KEY,
          web_user_id BIGINT NOT NULL,
          expires_at  TIMESTAMP NOT NULL,
          used        BOOLEAN DEFAULT FALSE,
          created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )

    # cat_collection: lifetime sticker counts per user, per cat type — now gifted
    # by an accountability buddy rather than earned in Pomewdoro
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cat_collection (
          user_id  BIGINT NOT NULL,
          cat_type TEXT NOT NULL,
          count    INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (user_id, cat_type)
        );
        """
    )

    # gift_tokens: single-use, expiring tokens issued in the nightly buddy report,
    # letting a buddy (who has no account) confirm the day's count and pick a
    # sticker for their student. report_date ties the token to the specific day
    # it's reporting on, so a confirmation lands on the right leaderboard date.
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS gift_tokens (
          token       TEXT PRIMARY KEY,
          user_id     BIGINT NOT NULL,
          report_date TEXT,
          expires_at  TIMESTAMP NOT NULL,
          used        BOOLEAN DEFAULT FALSE,
          created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    await conn.execute("ALTER TABLE gift_tokens ADD COLUMN IF NOT EXISTS report_date TEXT;")

    # confirmed_progress: the buddy-verified problem count for a given day —
    # distinct from daily_track.done (self-reported). Only a confirmed row makes
    # a student eligible for the leaderboard; no buddy means no confirmation,
    # which means no ranking, automatically.
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS confirmed_progress (
          user_id        BIGINT NOT NULL,
          date           TEXT NOT NULL,
          confirmed_done INTEGER NOT NULL,
          confirmed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (user_id, date)
        );
        """
    )

    # hearts_balance: spendable currency caught during Pomewdoro completion bursts
    await conn.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS hearts_balance INTEGER NOT NULL DEFAULT 0;")
    await conn.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS buddy_email TEXT;")

    # streak_saves: dates a user spent hearts to retroactively count as goal-met,
    # so a broken streak can be repaired instead of resetting to 0
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS streak_saves (
          user_id BIGINT NOT NULL,
          date    TEXT NOT NULL,
          PRIMARY KEY (user_id, date)
        );
        """
    )

    await conn.close()


async def create_magic_link(email: str) -> str:
    """Issue a 15-minute single-use sign-in token for an email address."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=15)
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO magic_links (token, email, expires_at) VALUES ($1, $2, $3)",
        token, email, expires_at
    )
    await conn.close()
    return token


async def consume_magic_link(token: str) -> str | None:
    """Validate and burn a token in one step. Returns the email, or None if invalid/expired/used."""
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT email, expires_at, used FROM magic_links WHERE token = $1", token
    )
    if not row or row["used"] or row["expires_at"] < datetime.utcnow():
        await conn.close()
        return None
    await conn.execute("UPDATE magic_links SET used = TRUE WHERE token = $1", token)
    await conn.close()
    return row["email"]


async def get_or_create_web_user(email: str) -> int:
    """
    Map an email to the shared user_id space used by daily_track/users/etc.

    Telegram user_ids are always positive, so web users are keyed on the
    *negative* of their web_users.id — guaranteed never to collide with a
    real Telegram id, with no coordination needed between the two tables.
    """
    conn = await get_pg_conn()
    row = await conn.fetchrow("SELECT id FROM web_users WHERE email = $1", email)
    if row:
        web_id = row["id"]
    else:
        row = await conn.fetchrow(
            "INSERT INTO web_users (email) VALUES ($1) RETURNING id", email
        )
        web_id = row["id"]
        default_name = email.split("@")[0]
        await conn.execute(
            "INSERT INTO users (user_id, username, first_name) VALUES ($1, '', $2) "
            "ON CONFLICT (user_id) DO NOTHING",
            -web_id, default_name
        )
    await conn.close()
    return -web_id

async def is_email_approved(email: str) -> bool:
    conn = await get_pg_conn()
    row = await conn.fetchval("SELECT 1 FROM approved_emails WHERE email = $1", email)
    await conn.close()
    return bool(row)


async def add_approved_email(email: str) -> None:
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO approved_emails (email) VALUES ($1) ON CONFLICT DO NOTHING", email
    )
    await conn.close()


async def remove_approved_email(email: str) -> None:
    conn = await get_pg_conn()
    await conn.execute("DELETE FROM approved_emails WHERE email = $1", email)
    await conn.close()


async def list_approved_emails() -> list[str]:
    conn = await get_pg_conn()
    rows = await conn.fetch("SELECT email FROM approved_emails ORDER BY added_at DESC")
    await conn.close()
    return [r["email"] for r in rows]


async def save_wrapup_log(content: str, date_, user_id=None):
    conn = await get_pg_conn()
    await conn.execute(
        """
        INSERT INTO wrapup_logs (date, user_id, content)
        VALUES ($1, $2, $3)
        """,
        date_, user_id, content
    )
    await conn.close()

async def get_user_profiles() -> dict[int, dict]:
    conn = await get_pg_conn()
    today = date.today()
    user_profiles = {}

    rows = await conn.fetch(
        "SELECT user_id, goal, done FROM daily_track WHERE date = $1",
        today.isoformat()
    )

    for row in rows:
        user_id = row["user_id"]
        goal = row["goal"]
        done = row["done"]

        streak = 0
        for offset in range(1, 8):
            d = today - timedelta(days=offset)
            record = await conn.fetchrow(
                "SELECT done FROM daily_track WHERE user_id = $1 AND date = $2",
                user_id, d.isoformat()
            )
            if record and record["done"] > 0:
                streak += 1
            else:
                break

        trait = "focused finisher" if done == goal else "resilient grinder" if done > 0 else "chill dreamer"

        user_profiles[user_id] = {
            "goal": goal,
            "done": done,
            "streak": streak,
            "trait": trait
        }

    await conn.close()
    return user_profiles


def _generate_link_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I — avoids confusion when typed
    return "".join(secrets.choice(alphabet) for _ in range(6))


async def create_link_code(web_user_id: int) -> str:
    """Issue a 15-minute single-use code the website shows and the bot's /link command consumes."""
    code = _generate_link_code()
    expires_at = datetime.utcnow() + timedelta(minutes=15)
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO link_codes (code, web_user_id, expires_at) VALUES ($1, $2, $3)",
        code, web_user_id, expires_at
    )
    await conn.close()
    return code


async def consume_link_code(code: str, telegram_user_id: int) -> bool:
    """Called from the bot's /link command. Returns True on a successful link."""
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT web_user_id, expires_at, used FROM link_codes WHERE code = $1", code.upper().strip()
    )
    if not row or row["used"] or row["expires_at"] < datetime.utcnow():
        await conn.close()
        return False
    await conn.execute("UPDATE link_codes SET used = TRUE WHERE code = $1", code.upper().strip())
    await conn.execute(
        "INSERT INTO account_links (web_user_id, telegram_user_id) VALUES ($1, $2) "
        "ON CONFLICT (web_user_id) DO UPDATE SET telegram_user_id = EXCLUDED.telegram_user_id, linked_at = CURRENT_TIMESTAMP",
        row["web_user_id"], telegram_user_id
    )
    await conn.close()
    return True


async def get_link_status(web_user_id: int) -> dict | None:
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT telegram_user_id, linked_at FROM account_links WHERE web_user_id = $1", web_user_id
    )
    await conn.close()
    return dict(row) if row else None


async def get_reminders_enabled(user_id: int) -> bool:
    conn = await get_pg_conn()
    row = await conn.fetchrow("SELECT reminders_enabled FROM user_preferences WHERE user_id = $1", user_id)
    await conn.close()
    return bool(row["reminders_enabled"]) if row else True


async def set_reminders_enabled(user_id: int, enabled: bool) -> None:
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO user_preferences (user_id, reminders_enabled) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET reminders_enabled = EXCLUDED.reminders_enabled",
        user_id, enabled
    )
    await conn.close()


async def get_streak(user_id: int) -> int:
    """Current daily streak for one user. A day counts if the goal was met, OR the
    day was repaired with hearts (see streak_saves).

    Today only anchors the walk if it already counts — otherwise today is still in
    progress and shouldn't zero out an existing streak just for not being done yet.
    """
    conn = await get_pg_conn()
    today = date.today()
    saved_rows = await conn.fetch("SELECT date FROM streak_saves WHERE user_id = $1", user_id)
    saved_dates = {r["date"] for r in saved_rows}

    async def day_counts(d):
        d_str = d.isoformat()
        row = await conn.fetchrow(
            "SELECT goal, done FROM daily_track WHERE user_id = $1 AND date = $2",
            user_id, d_str
        )
        return bool(row and row["goal"] and row["done"] >= row["goal"]) or d_str in saved_dates

    streak = 0
    d = today if await day_counts(today) else today - timedelta(days=1)
    for _ in range(365):
        if await day_counts(d):
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    await conn.close()
    return streak


async def get_repairable_date(user_id: int) -> str | None:
    """The single most recent PAST day that broke the streak, if any and if it's
    still fresh enough to repair (yesterday only, like a streak freeze). Starts
    from yesterday, not today — today is still in progress and never counts as
    "broken" just because it hasn't been logged yet."""
    conn = await get_pg_conn()
    today = date.today()
    saved_rows = await conn.fetch("SELECT date FROM streak_saves WHERE user_id = $1", user_id)
    saved_dates = {r["date"] for r in saved_rows}

    # walk back through days that already count, to find the first one that doesn't
    d = today - timedelta(days=1)
    for _ in range(365):
        d_str = d.isoformat()
        row = await conn.fetchrow(
            "SELECT goal, done FROM daily_track WHERE user_id = $1 AND date = $2",
            user_id, d_str
        )
        goal_met = bool(row and row["goal"] and row["done"] >= row["goal"])
        if goal_met or d_str in saved_dates:
            d -= timedelta(days=1)
        else:
            break
    await conn.close()

    if (today - d).days <= 1:
        return d.isoformat()
    return None


async def get_history(user_id: int, days: int = 182) -> list[dict]:
    """Daily goal/done for a rolling window, oldest first — feeds the contribution heatmap."""
    conn = await get_pg_conn()
    start = date.today() - timedelta(days=days - 1)
    rows = await conn.fetch(
        "SELECT date, goal, done FROM daily_track WHERE user_id = $1 AND date >= $2 ORDER BY date",
        user_id, start.isoformat()
    )
    await conn.close()
    by_date = {r["date"]: {"goal": r["goal"], "done": r["done"]} for r in rows}

    history = []
    for i in range(days):
        d = start + timedelta(days=i)
        entry = by_date.get(d.isoformat())
        history.append({
            "date": d,
            "goal": entry["goal"] if entry else 0,
            "done": entry["done"] if entry else 0,
        })
    return history


async def add_cats(user_id: int, cat_type: str, count: int) -> None:
    if count <= 0:
        return
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO cat_collection (user_id, cat_type, count) VALUES ($1, $2, $3) "
        "ON CONFLICT (user_id, cat_type) DO UPDATE SET count = cat_collection.count + EXCLUDED.count",
        user_id, cat_type, count
    )
    await conn.close()


async def get_collection(user_id: int) -> dict[str, int]:
    conn = await get_pg_conn()
    rows = await conn.fetch("SELECT cat_type, count FROM cat_collection WHERE user_id = $1", user_id)
    await conn.close()
    return {r["cat_type"]: r["count"] for r in rows}


async def get_hearts_balance(user_id: int) -> int:
    conn = await get_pg_conn()
    row = await conn.fetchrow("SELECT hearts_balance FROM user_preferences WHERE user_id = $1", user_id)
    await conn.close()
    return row["hearts_balance"] if row else 0


async def add_hearts(user_id: int, count: int) -> None:
    if count <= 0:
        return
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO user_preferences (user_id, hearts_balance) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET hearts_balance = user_preferences.hearts_balance + EXCLUDED.hearts_balance",
        user_id, count
    )
    await conn.close()


async def spend_hearts(user_id: int, amount: int) -> bool:
    """Atomically deducts hearts only if the balance covers it (single statement — no
    read-then-write race). Returns whether the spend succeeded."""
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "UPDATE user_preferences SET hearts_balance = hearts_balance - $2 "
        "WHERE user_id = $1 AND hearts_balance >= $2 RETURNING hearts_balance",
        user_id, amount
    )
    await conn.close()
    return row is not None


async def transfer_hearts(sender_id: int, recipient_id: int, amount: int) -> bool:
    """Moves hearts from sender to recipient atomically. Returns whether it succeeded."""
    if amount <= 0 or sender_id == recipient_id:
        return False
    conn = await get_pg_conn()
    success = False
    async with conn.transaction():
        sender_row = await conn.fetchrow(
            "SELECT hearts_balance FROM user_preferences WHERE user_id = $1 FOR UPDATE",
            sender_id
        )
        balance = sender_row["hearts_balance"] if sender_row else 0
        if balance >= amount:
            await conn.execute(
                "UPDATE user_preferences SET hearts_balance = hearts_balance - $2 WHERE user_id = $1",
                sender_id, amount
            )
            await conn.execute(
                "INSERT INTO user_preferences (user_id, hearts_balance) VALUES ($1, $2) "
                "ON CONFLICT (user_id) DO UPDATE SET hearts_balance = user_preferences.hearts_balance + EXCLUDED.hearts_balance",
                recipient_id, amount
            )
            success = True
    await conn.close()
    return success


async def save_streak_date(user_id: int, date_str: str) -> None:
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO streak_saves (user_id, date) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, date_str
    )
    await conn.close()


async def get_saved_dates(user_id: int) -> set[str]:
    conn = await get_pg_conn()
    rows = await conn.fetch("SELECT date FROM streak_saves WHERE user_id = $1", user_id)
    await conn.close()
    return {r["date"] for r in rows}


async def get_buddy_email(user_id: int) -> str | None:
    conn = await get_pg_conn()
    row = await conn.fetchrow("SELECT buddy_email FROM user_preferences WHERE user_id = $1", user_id)
    await conn.close()
    return row["buddy_email"] if row else None


async def set_buddy_email(user_id: int, email: str | None) -> None:
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO user_preferences (user_id, buddy_email) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET buddy_email = EXCLUDED.buddy_email",
        user_id, email
    )
    await conn.close()


async def get_all_buddy_emails() -> list[tuple[int, str]]:
    """Every (user_id, buddy_email) pair with a buddy set — works across web and
    Telegram users alike, since both live in the same user_preferences table."""
    conn = await get_pg_conn()
    rows = await conn.fetch(
        "SELECT user_id, buddy_email FROM user_preferences WHERE buddy_email IS NOT NULL AND buddy_email != ''"
    )
    await conn.close()
    return [(r["user_id"], r["buddy_email"]) for r in rows]


async def get_daily_summary(user_id: int) -> dict:
    """Today's goal/done plus current streak, for the nightly buddy report."""
    conn = await get_pg_conn()
    today = date.today().isoformat()
    name_row = await conn.fetchrow(
        "SELECT COALESCE(NULLIF(username, ''), first_name) AS display_name FROM users WHERE user_id = $1",
        user_id
    )
    today_row = await conn.fetchrow(
        "SELECT goal, done FROM daily_track WHERE user_id = $1 AND date = $2", user_id, today
    )
    await conn.close()
    display_name = name_row["display_name"] if name_row and name_row["display_name"] else "your student"
    return {
        "display_name": display_name,
        "goal": today_row["goal"] if today_row else 0,
        "done": today_row["done"] if today_row else 0,
        "streak": await get_streak(user_id),
    }


async def create_gift_token(user_id: int, report_date: str, hours_valid: int = 48) -> str:
    """Issued in the nightly buddy report — the buddy has no account, so this
    token is how a single email link authenticates the gift-picker page.
    report_date ties it to the specific day being confirmed."""
    token = secrets.token_urlsafe(24)
    expires_at = datetime.utcnow() + timedelta(hours=hours_valid)
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO gift_tokens (token, user_id, report_date, expires_at) VALUES ($1, $2, $3, $4)",
        token, user_id, report_date, expires_at
    )
    await conn.close()
    return token


async def get_gift_token_info(token: str) -> dict | None:
    """Validates without consuming — used to render the picker page itself,
    which a buddy should be able to load and browse before committing to a gift."""
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT user_id, report_date, expires_at, used FROM gift_tokens WHERE token = $1", token
    )
    await conn.close()
    if not row or row["used"] or row["expires_at"] < datetime.utcnow():
        return None
    return {"user_id": row["user_id"], "report_date": row["report_date"]}


async def consume_gift_token(token: str) -> int | None:
    """Validates and burns the token in one step — used only when a sticker is
    actually awarded, so viewing the picker page never spends the one gift."""
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT user_id, expires_at, used FROM gift_tokens WHERE token = $1", token
    )
    if not row or row["used"] or row["expires_at"] < datetime.utcnow():
        await conn.close()
        return None
    await conn.execute("UPDATE gift_tokens SET used = TRUE WHERE token = $1", token)
    await conn.close()
    return row["user_id"]


async def save_confirmed_progress(user_id: int, date_str: str, confirmed_done: int) -> None:
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO confirmed_progress (user_id, date, confirmed_done) VALUES ($1, $2, $3) "
        "ON CONFLICT (user_id, date) DO UPDATE SET confirmed_done = EXCLUDED.confirmed_done, "
        "confirmed_at = CURRENT_TIMESTAMP",
        user_id, date_str, confirmed_done
    )
    await conn.close()


async def get_confirmed_progress(user_id: int, date_str: str) -> int | None:
    conn = await get_pg_conn()
    row = await conn.fetchrow(
        "SELECT confirmed_done FROM confirmed_progress WHERE user_id = $1 AND date = $2",
        user_id, date_str
    )
    await conn.close()
    return row["confirmed_done"] if row else None


async def get_confirmed_leaderboard(target_date: str, limit: int = 10) -> list[dict]:
    """Ranks students by their buddy-confirmed count for one specific day. Only
    students with a confirmed row for that date appear at all — no buddy (or a
    buddy who hasn't confirmed yet) means no ranking, not a zero."""
    conn = await get_pg_conn()
    rows = await conn.fetch(
        """
        SELECT cp.user_id, cp.confirmed_done,
               COALESCE(NULLIF(u.username, ''), u.first_name) AS display_name
        FROM confirmed_progress cp
        JOIN users u ON u.user_id = cp.user_id
        WHERE cp.date = $1
        ORDER BY cp.confirmed_done DESC
        LIMIT $2
        """,
        target_date, limit
    )
    await conn.close()

    results = []
    for r in rows:
        results.append({
            "user_id": r["user_id"],
            "display_name": r["display_name"] or "someone",
            "confirmed_done": r["confirmed_done"],
            "streak": await get_streak(r["user_id"]),
        })
    return results
