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
    # avatar_emoji added after the table already existed in earlier deployments
    await conn.execute("ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS avatar_emoji TEXT;")

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

    # cat_collection: lifetime Pomewdoro cat counts per user, per cat type
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


async def get_avatar_emoji(user_id: int) -> str | None:
    conn = await get_pg_conn()
    row = await conn.fetchrow("SELECT avatar_emoji FROM user_preferences WHERE user_id = $1", user_id)
    await conn.close()
    return row["avatar_emoji"] if row else None


async def set_avatar_emoji(user_id: int, emoji: str) -> None:
    conn = await get_pg_conn()
    await conn.execute(
        "INSERT INTO user_preferences (user_id, avatar_emoji) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET avatar_emoji = EXCLUDED.avatar_emoji",
        user_id, emoji
    )
    await conn.close()


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
    """Current daily streak for one user (goal must be set and met each day)."""
    conn = await get_pg_conn()
    today = date.today()
    streak = 0
    d = today
    for _ in range(365):
        row = await conn.fetchrow(
            "SELECT goal, done FROM daily_track WHERE user_id = $1 AND date = $2",
            user_id, d.isoformat()
        )
        if row and row["goal"] and row["done"] >= row["goal"]:
            streak += 1
            d -= timedelta(days=1)
        else:
            break
    await conn.close()
    return streak


async def get_full_leaderboard(limit: int = 10) -> list[dict]:
    """
    Ranks every user (Telegram + web) by current streak, then all-time total.
    Linked accounts (see account_links) are merged into a single row: totals
    are summed and the streak counts a day if EITHER surface hit its goal.
    """
    conn = await get_pg_conn()

    id_rows = await conn.fetch("SELECT DISTINCT user_id FROM daily_track")
    all_ids = [r["user_id"] for r in id_rows]

    link_rows = await conn.fetch("SELECT web_user_id, telegram_user_id FROM account_links")
    canonical = {r["telegram_user_id"]: r["web_user_id"] for r in link_rows}

    groups: dict[int, list[int]] = {}
    for uid in all_ids:
        cid = canonical.get(uid, uid)
        groups.setdefault(cid, []).append(uid)
    # a linked web account with no daily_track rows of its own still needs its id in the group
    for cid in groups:
        if cid not in groups[cid]:
            groups[cid].append(cid)

    today = date.today()
    results = []

    for cid, member_ids in groups.items():
        total_row = await conn.fetchrow(
            "SELECT COALESCE(SUM(done), 0) AS total FROM daily_track WHERE user_id = ANY($1::BIGINT[])",
            member_ids
        )
        total = total_row["total"]

        streak = 0
        d = today
        for _ in range(365):  # safety bound — no one has a 365-day streak yet
            day_row = await conn.fetchrow(
                "SELECT COALESCE(SUM(done), 0) AS done, COALESCE(SUM(goal), 0) AS goal "
                "FROM daily_track WHERE user_id = ANY($1::BIGINT[]) AND date = $2",
                member_ids, d.isoformat()
            )
            if day_row["goal"] and day_row["done"] >= day_row["goal"]:
                streak += 1
                d -= timedelta(days=1)
            else:
                break

        display_name = None
        for candidate_id in [cid] + member_ids:
            nr = await conn.fetchrow(
                "SELECT COALESCE(NULLIF(username, ''), first_name) AS display_name FROM users WHERE user_id = $1",
                candidate_id
            )
            if nr and nr["display_name"]:
                display_name = nr["display_name"]
                break

        avatar_row = await conn.fetchrow(
            "SELECT avatar_emoji FROM user_preferences WHERE user_id = ANY($1::BIGINT[]) AND avatar_emoji IS NOT NULL LIMIT 1",
            member_ids
        )
        avatar = avatar_row["avatar_emoji"] if avatar_row else None

        results.append({
            "user_id": cid,
            "member_ids": member_ids,
            "display_name": display_name or "someone",
            "streak": streak,
            "total": total,
            "avatar": avatar,
        })

    await conn.close()
    results.sort(key=lambda r: (-r["streak"], -r["total"]))
    return results[:limit]


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
