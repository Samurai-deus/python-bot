"""
Клиент OpenRouter для ИИ-трейдера (docs/AI_TRADER_PLAN.md).

Один вызов — один запрос chat/completions. Перед вызовом проверяется суточный
бюджет, после — стоимость пишется в ai_usage (OpenRouter возвращает её в usage
при usage.include; если не вернул — оценка по цене модели). Любой сбой — None:
для торговли это «мнения нет».

Второй предохранитель бюджета — лимит кредита на самом ключе OpenRouter: запрос,
который биржа моделей исполнила, но ответ которого потерялся, здесь не учтётся.
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, UTC
from typing import Optional

import httpx

from utils.env import env_float, env_str

logger = logging.getLogger(__name__)

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_REVIEW_MODEL = "anthropic/claude-sonnet-5"
DEFAULT_CHAT_MODEL = "anthropic/claude-opus-5"
DEFAULT_DAILY_BUDGET_USD = 1.0
TIMEOUT_SECONDS = 40.0

# Цена за миллион токенов (вход, выход) по каталогу OpenRouter на 11.09.2026 —
# только для оценки, если стоимость не пришла в ответе. Неизвестная модель — по
# дорогой ставке: лучше остановиться раньше, чем превысить бюджет.
_FALLBACK_PRICES = {
    "anthropic/claude-sonnet-5": (2.0, 10.0),
    "anthropic/claude-opus-5": (5.0, 25.0),
}
_UNKNOWN_PRICE = (10.0, 50.0)


@dataclass
class Completion:
    text: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    finish_reason: Optional[str] = None  # 'length' — ответ оборван по max_tokens


def api_key() -> str:
    return env_str("OPENROUTER_API_KEY", "").strip()


def review_model() -> str:
    return env_str("AI_MODEL_REVIEW", "").strip() or DEFAULT_REVIEW_MODEL


def chat_model() -> str:
    return env_str("AI_MODEL_CHAT", "").strip() or DEFAULT_CHAT_MODEL


def daily_budget_usd() -> float:
    return env_float("AI_DAILY_BUDGET_USD", DEFAULT_DAILY_BUDGET_USD)


def utc_day(now: Optional[datetime] = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def budget_left_usd(now: Optional[datetime] = None) -> float:
    from database import get_ai_spend
    return daily_budget_usd() - get_ai_spend(utc_day(now))


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price_in, price_out = _FALLBACK_PRICES.get(model, _UNKNOWN_PRICE)
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000


def complete(purpose: str, system: str, user: str, model: str, max_tokens: int = 700,
             transport: Optional[httpx.BaseTransport] = None) -> Optional[Completion]:
    """Запрос к модели. None — ключа нет, бюджет исчерпан или OpenRouter не ответил как положено."""
    key = api_key()
    if not key:
        return None
    try:
        left = budget_left_usd()
    except Exception:
        logger.warning("ai_trader: расход за сутки не прочитан — %s пропущен", purpose, exc_info=True)
        return None
    if left <= 0:
        logger.info("ai_trader: суточный бюджет %.2f $ исчерпан — %s пропущен", daily_budget_usd(), purpose)
        return None

    body = {
        "model": model,
        "messages": [
            # Инструкция неизменна — кэш промпта (для Claude на OpenRouter — cache_control).
            {"role": "system", "content": [{"type": "text", "text": system,
                                            "cache_control": {"type": "ephemeral"}}]},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "usage": {"include": True},
    }
    # С IP сервера OpenRouter напрямую отвечает 403 — ходим через прокси хоста,
    # как Telegram. Подставной транспорт (тесты) прокси не нужен.
    proxy = (env_str("AI_PROXY_URL", "").strip() or None) if transport is None else None
    started = time.monotonic()
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, transport=transport, proxy=proxy) as http:
            response = http.post(API_URL, json=body,
                                 headers={"Authorization": f"Bearer {key}", "X-Title": "market-bot"})
        response.raise_for_status()
        data = response.json()
        text = data["choices"][0]["message"]["content"] or ""
        finish_reason = data["choices"][0].get("finish_reason")
        usage = data.get("usage") or {}
    except Exception as exc:
        # Только тип ошибки: в тексте исключения httpx бывает URL с телом ответа, ключ — в заголовке.
        logger.warning("ai_trader: %s — OpenRouter не ответил как положено: %s", purpose, type(exc).__name__)
        return None

    latency_ms = int((time.monotonic() - started) * 1000)
    used_model = data.get("model") or model
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cost = usage.get("cost")
    cost_usd = float(cost) if cost is not None else estimate_cost(model, prompt_tokens, completion_tokens)
    try:
        from database import record_ai_usage
        record_ai_usage(utc_day(), purpose, used_model, prompt_tokens, completion_tokens, cost_usd)
    except Exception:
        logger.warning("ai_trader: расход %.4f $ не записан", cost_usd, exc_info=True)
    return Completion(text=str(text), model=used_model, prompt_tokens=prompt_tokens,
                      completion_tokens=completion_tokens, cost_usd=cost_usd, latency_ms=latency_ms,
                      finish_reason=finish_reason)
