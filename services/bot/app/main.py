"""Telegram bot entry point: Telegram Mini App launcher and travel assistant."""
from __future__ import annotations

import logging
import sys

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)


def _planner_keyboard() -> InlineKeyboardMarkup:
    web_app = WebAppInfo(url=settings.web_app_url)
    keyboard = [
        [InlineKeyboardButton("Открыть AI-планировщик 🌍", web_app=web_app)]
    ]
    return InlineKeyboardMarkup(keyboard)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    text = (
        "Привет! Я AI-планировщик путешествий.\n\n"
        "Помогу составить маршрут, подобрать отели и перелёты с учётом бюджета и предпочтений.\n"
        "Нажмите кнопку ниже, чтобы открыть интерактивный планировщик."
    )
    await update.effective_message.reply_text(text, reply_markup=_planner_keyboard())


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    text = (
        "Чтобы спланировать поездку с проверкой цен, дат и ограничений, "
        "откройте приложение по кнопке ниже:"
    )
    await update.effective_message.reply_text(text, reply_markup=_planner_keyboard())


def main() -> None:
    token = settings.telegram_bot_token.strip()
    if not token:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is not set. Bot service will not poll Telegram. "
            "Set TELEGRAM_BOT_TOKEN in .env to enable the bot."
        )
        sys.exit(0)

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Starting Telegram bot polling...")
    app.run_polling()


if __name__ == "__main__":
    main()

