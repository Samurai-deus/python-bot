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
# Проверка наличия токенов перенесена в main(): импорт модуля падать не должен.

# Доступ решает utils.principals: владелец (ADMIN_CHAT_ID) плюс ALLOWED_USER_IDS.
# Раньше пустой ALLOWED_USER_IDS означал «пускать всех» — посторонние гоняли бы
# дорогую модель за счёт владельца, а история каждого хранилась в памяти.

# Сообщений в минуту на пользователя — страховка от залпа, который превращается
# в счёт за API.
RATE_LIMIT_PER_MINUTE = int(os.getenv("CLAUDE_RATE_LIMIT_PER_MINUTE", "10"))
_recent_requests: dict[int, list[float]] = {}

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
    """Доступ: владелец или ALLOWED_USER_IDS. Пустая настройка — никому."""
    from utils import principals
    return principals.is_allowed(user_id)


def within_rate_limit(user_id: int, now: float | None = None) -> bool:
    """Скользящее окно в одну минуту на пользователя."""
    import time as _time
    now = _time.time() if now is None else now
    window = [t for t in _recent_requests.get(user_id, []) if now - t < 60]
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        _recent_requests[user_id] = window
        return False
    window.append(now)
    _recent_requests[user_id] = window
    return True


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

    if not within_rate_limit(user.id):
        await update.message.reply_text("⚠️ Слишком часто. Подожди минуту.")
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
    except Exception:
        # Текст исключения наружу не отдаём: в ответах API бывают идентификаторы
        # запросов и сведения об организации. Подробности — в лог.
        logger.exception("Unexpected error from Claude API")
        reply = "⚠️ Внутренняя ошибка. Попробуй позже."

    # Telegram ограничивает сообщения до 4096 символов
    if len(reply) <= 4096:
        await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        # Разбиваем на части по 4096 символов
        for i in range(0, len(reply), 4096):
            await update.message.reply_text(reply[i : i + 4096], parse_mode="Markdown")


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    from utils import principals
    if not TELEGRAM_TOKEN:
        raise SystemExit("TELEGRAM_BOT_TOKEN is required — set it in .env")
    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is required — set it in .env")
    principals.require_configured()

    logger.info("Starting Claude Assistant bot (model=%s)", MODEL)
    logger.info("Access: owner + %d observer(s)", max(0, len(principals.allowed_ids()) - 1))

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
