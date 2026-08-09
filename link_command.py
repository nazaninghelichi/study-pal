import logging
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from db import consume_link_code

logger = logging.getLogger(__name__)

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text(
            "Usage: `/link CODE` — get your code from Settings on the website.",
            parse_mode="Markdown"
        )
        return

    code = context.args[0]
    success = await consume_link_code(code, user_id)
    if success:
        logger.info(f"/link succeeded for telegram user {user_id}")
        await update.message.reply_text(
            "✅ Linked! Your streak and totals now combine across the bot and the website."
        )
    else:
        await update.message.reply_text(
            "❌ That code is invalid or expired. Generate a new one from Settings on the website."
        )

def get_link_handler():
    return CommandHandler("link", link_command)
