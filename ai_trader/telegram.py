"""
Команды ИИ-трейдера в основном боте: /ai — вопрос модели, /ai_stats — расход и
точность теневых мнений. Допуск — общий фильтр бота (telegram_commands,
группа -1): посторонним бот не отвечает вовсе.
"""
import asyncio
import json
import logging
import time

logger = logging.getLogger(__name__)

MIN_SECONDS_BETWEEN_QUESTIONS = 10.0
_TELEGRAM_LIMIT = 4000
_last_question_at = {}


def ask(question: str, transport=None) -> str:
    """Ответ модели на вопрос владельца в контексте портфеля и последних мнений."""
    from ai_trader import client, prompts, review
    import database

    try:
        context = {"portfolio": review.portfolio_context(),
                   "recent_ai_opinions": database.get_recent_ai_opinions(5)}
    except Exception as exc:
        logger.debug("ai_trader: контекст для чата не собран: %s", exc)
        context = {}
    user = question + "\n\nКонтекст системы (JSON):\n" + json.dumps(context, ensure_ascii=False, default=str)
    completion = client.complete("chat", prompts.CHAT_SYSTEM, user, client.chat_model(),
                                 max_tokens=1200, transport=transport)
    if completion is None:
        return ("Ответа нет: ИИ не подключён (нет OPENROUTER_API_KEY), исчерпан суточный бюджет "
                "или OpenRouter не ответил. Подробности — /ai_stats.")
    return completion.text.strip() or "Модель вернула пустой ответ."


def stats_text(days: int = 7) -> str:
    from ai_trader import client
    from ai_trader.worker import stage
    import database

    spent = database.get_ai_spend(client.utc_day())
    stats = database.get_ai_opinion_stats(days)
    lines = [
        f"🤖 ИИ-трейдер: этап {stage()}",
        f"Расход сегодня: {spent:.3f} $ из {client.daily_budget_usd():.2f} $",
        f"Модели: оценка — {client.review_model()}, чат — {client.chat_model()}",
        "",
        f"Мнения за {days} дн.:",
    ]
    if not stats:
        lines.append("пока нет")
    for decision, row in sorted(stats.items()):
        outcomes = ", ".join(f"{k} {v}" for k, v in sorted(row["outcomes"].items())) or "исходов ещё нет"
        lines.append(f"• {decision}: {row['total']} — {outcomes}")
    lines += ["", "Главная метрика этапа 0: сигналы, которые ИИ отклонил бы, должны отрабатывать хуже одобренных."]
    return "\n".join(lines)


async def _reply(update, text: str) -> None:
    for start in range(0, len(text), _TELEGRAM_LIMIT):
        await update.message.reply_text(text[start:start + _TELEGRAM_LIMIT])


async def cmd_ai(update, context) -> None:
    question = " ".join(context.args or []).strip()
    if not question:
        await _reply(update, "Задайте вопрос после команды: /ai что сейчас с рынком и моими позициями?")
        return
    user_id = update.effective_user.id if update.effective_user else 0
    now = time.monotonic()
    if now - _last_question_at.get(user_id, -MIN_SECONDS_BETWEEN_QUESTIONS) < MIN_SECONDS_BETWEEN_QUESTIONS:
        await _reply(update, "Не чаще одного вопроса в 10 секунд.")
        return
    _last_question_at[user_id] = now
    answer = await asyncio.to_thread(ask, question)
    await _reply(update, answer)


async def cmd_ai_stats(update, context) -> None:
    try:
        text = await asyncio.to_thread(stats_text)
    except Exception:
        logger.warning("ai_trader: статистика не собрана", exc_info=True)
        text = "Статистика ИИ сейчас недоступна."
    await _reply(update, text)
