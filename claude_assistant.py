"""
Claude Assistant — Telegram bot backed by Claude API.

Usage:
    pip install anthropic python-telegram-bot python-dotenv
    python claude_assistant.py

Commands:
    /start  — приветствие
    /reset  — сбросить историю разговора
    /help   — справка

Config (в .env или переменных окружения):
    TELEGRAM_BOT_TOKEN   — токен бота (обязательно)
    ANTHROPIC_API_KEY    — ключ Claude API (обязательно)
    ALLOWED_USER_IDS     — разрешённые Telegram user_id через запятую (необязательно)
                           Если не задано — бот отвечает всем.
"""

import logging
import os

import anthropic
from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN is required — set it in .env")
if not ANTHROPIC_API_KEY:
    raise ValueError("ANTHROPIC_API_KEY is required — set it in .env")

_raw_ids = os.getenv("ALLOWED_USER_IDS", "")
ALLOWED_USER_IDS: set[int] = (
    {int(x.strip()) for x in _raw_ids.split(",") if x.strip()}
    if _raw_ids.strip()
    else set()
)

MODEL = "claude-opus-4-6"
MAX_TOKENS = 4096
# Hard limit on stored messages per user to prevent unbounded memory growth.
# Each message is ~1–4 KB; 100 messages ≈ 400 KB per active user.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "100"))

SYSTEM_PROMPT = """Ты — персональный ИИ-ассистент в Telegram.
Отвечай на русском, если не попросят иначе.
Будь краток и по делу. Используй markdown где уместно (жирный, курсив, код).
Если задаётся технический вопрос — пиши конкретный код, не абстрактные объяснения."""

# ── State ─────────────────────────────────────────────────────────────────────

# Словарь user_id → список сообщений (история разговора)
conversations: dict[int, list[dict]] = {}

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# ── Helpers ───────────────────────────────────────────────────────────────────


def is_allowed(user_id: int) -> bool:
    """Проверяет доступ. Если ALLOWED_USER_IDS пустой — пускает всех."""
    return not ALLOWED_USER_IDS or user_id in ALLOWED_USER_IDS


def get_history(user_id: int) -> list[dict]:
    return conversations.setdefault(user_id, [])


def add_message(user_id: int, role: str, content: str) -> None:
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    # Trim oldest messages (keep pairs: drop user+assistant together)
    while len(history) > MAX_HISTORY_MESSAGES:
        history.pop(0)


def reset_history(user_id: int) -> None:
    conversations[user_id] = []


def ask_claude(user_id: int, user_text: str) -> str:
    """Добавляет сообщение пользователя, вызывает Claude, возвращает ответ."""
    add_message(user_id, "user", user_text)

    response = claude.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=get_history(user_id),
    )

    reply = response.content[0].text
    add_message(user_id, "assistant", reply)
    return reply


# ── Handlers ──────────────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    reset_history(user.id)
    await update.message.reply_text(
        f"Привет, {user.first_name}! Я Claude — спрашивай что угодно.\n"
        "/reset — сбросить историю\n"
        "/help — справка"
    )


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user.id):
        return

    reset_history(user.id)
    await update.message.reply_text("История сброшена. Начинаем заново.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        return

    await update.message.reply_text(
        "*Команды:*\n"
        "/start — начать разговор\n"
        "/reset — сбросить историю\n"
        "/help — эта справка\n\n"
        f"*Модель:* `{MODEL}`\n"
        "*История:* сохраняется до /reset или перезапуска бота.",
        parse_mode="Markdown",
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not is_allowed(user.id):
        await update.message.reply_text("⛔ Нет доступа.")
        return

    user_text = update.message.text.strip()
    if not user_text:
        return

    # Показываем «печатает...»
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action=ChatAction.TYPING
    )

    try:
        reply = ask_claude(user.id, user_text)
    except anthropic.RateLimitError:
        reply = "⚠️ Превышен лимит запросов. Подожди немного и попробуй снова."
    except anthropic.APIConnectionError:
        reply = "⚠️ Ошибка соединения с Claude API. Проверь интернет."
    except Exception as e:
        logger.exception("Unexpected error from Claude API")
        reply = f"⚠️ Ошибка: {e}"

    # Telegram ограничивает сообщения до 4096 символов
    if len(reply) <= 4096:
        await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        # Разбиваем на части по 4096 символов
        for i in range(0, len(reply), 4096):
            await update.message.reply_text(reply[i : i + 4096], parse_mode="Markdown")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    logger.info("Starting Claude Assistant bot (model=%s)", MODEL)
    if ALLOWED_USER_IDS:
        logger.info("Access restricted to user IDs: %s", ALLOWED_USER_IDS)
    else:
        logger.warning("ALLOWED_USER_IDS not set — bot is open to everyone!")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
