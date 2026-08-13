from telegram import Update
from telegram.ext import ContextTypes
import logging
from datetime import date, timedelta
from db import get_confirmed_leaderboard
from telegram import ReplyKeyboardMarkup

logger = logging.getLogger(__name__)

async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show yesterday's buddy-confirmed leaderboard — only students whose
    accountability buddy confirmed a count for that day appear.
    """
    board_date = (date.today() - timedelta(days=1)).isoformat()
    rows = await get_confirmed_leaderboard(board_date)

    if not rows:
        text = (
            f"📋 No confirmed results for {board_date} yet — ask your "
            "accountability buddy to confirm your count in Settings."
        )
    else:
        text_lines = [f"🏆 *Buddy-Confirmed Leaderboard ({board_date}):*", ""]
        for rank, row in enumerate(rows, start=1):
            medal = {1: '🥇', 2: '🥈', 3: '🥉'}.get(rank, f"{rank}.")
            text_lines.append(
                f"{medal} *{row['display_name']}* — {row['confirmed_done']} confirmed "
                f"(streak {row['streak']})"
            )
        text = "\n".join(text_lines)

    # Include a Home button
    home_kb = ReplyKeyboardMarkup([['🏠 Home']], resize_keyboard=True)

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=home_kb
    )
