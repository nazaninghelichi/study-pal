from telegram import Update
from telegram.ext import ContextTypes
import logging
from datetime import date
from db import get_db_connection
from telegram import ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fetch and display the top 5 users by jobs logged for today from the database.
    """
    # Connect to Postgres
    conn = await get_db_connection()
    today_str = date.today().isoformat()

    # Query top performers from Postgres
    rows = await conn.fetch(
        """
        SELECT u.user_id,
               COALESCE(NULLIF(u.username, ''), u.first_name) AS display_name,
               dt.done
        FROM daily_track dt
        JOIN users u ON u.user_id = dt.user_id
        WHERE dt.date = $1 AND dt.done > 0
        ORDER BY dt.done DESC
        LIMIT 5;
        """,
        today_str
    )
    await conn.close()

    # Build response text
    if not rows:
        text = "📋 No one has logged study sessions today yet."
    else:
        text_lines = [f"🏆 *Today's Top Studiers ({today_str}):*", ""]
        for rank, row in enumerate(rows, start=1):
            medal = {1: '🥇', 2: '🥈', 3: '🥉'}.get(rank, f"{rank}.")
            text_lines.append(f"{medal} *{row['display_name']}* — {row['done']} logged")
        text = "\n".join(text_lines)

    # Include a Home button
    home_kb = ReplyKeyboardMarkup([['🏠 Home']], resize_keyboard=True)

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=home_kb
    )
