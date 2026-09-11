"""
Фоновая очередь мнений ИИ (этап 0 — тень).

Гейткипер кладёт одобренный правилами сигнал и сразу идёт дальше: у рыночного
цикла жёсткий бюджет 60 с на итерацию, модель в нём не ждут. Очередь ограничена:
если модель не успевает, лишние сигналы остаются без мнения, а не копятся.

Мнение пишется в ai_opinions с ключом (signal_ts, symbol) — тем же, по которому
outcome_tracker размечает исход сигнала. Владельцу приходит сообщение, только
когда ИИ не согласен с правилами: сотня сообщений «одобрил бы» в сутки — шум.
"""
import logging
import queue
import threading
from typing import Callable, Optional

from utils.env import env_str

logger = logging.getLogger(__name__)

QUEUE_SIZE = 20
# Ответ оценки — JSON с пятью причинами по-русски; кириллица дорогая в токенах, и при
# 700 ответ обрывался на полуслове: все мнения 11.09.2026 записались как bad_format.
REVIEW_MAX_TOKENS = 1500
# Сигналы, которые система не взяла (шаг 5 плана обучения), оцениваются, только пока от
# суточного бюджета осталось больше этой доли, и занимают не больше половины очереди:
# мнение по взятому сигналу нужнее.
UNSENT_BUDGET_RESERVE = 0.5
SENT = "SENT"

_queue: "queue.Queue[dict]" = queue.Queue(maxsize=QUEUE_SIZE)
_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()
_warned = set()


def _warn_once(key: str, msg: str, *args) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg, *args)


def stage() -> str:
    """'off' — ИИ выключен; '0' — тень. Этапы выше ещё не реализованы и работают как тень."""
    from ai_trader.client import api_key
    if not api_key():
        return "off"
    value = (env_str("AI_TRADER_STAGE", "0") or "0").strip().lower()
    if value == "off":
        return "off"
    if value != "0":
        _warn_once("stage", "ai_trader: этап %s ещё не реализован — работаю в тени (этап 0)", value)
    return "0"


def _room_for_unsent() -> bool:
    """Есть ли место для сигнала, который система не взяла: очередь и резерв бюджета."""
    from ai_trader.client import budget_left_usd, daily_budget_usd
    if _queue.qsize() >= QUEUE_SIZE // 2:
        return False
    try:
        return budget_left_usd() > daily_budget_usd() * UNSENT_BUDGET_RESERVE
    except Exception:
        return False


def submit(symbol: str, signal_data: dict, snapshot, fate: str = SENT, signal_ts: Optional[str] = None) -> bool:
    """
    Поставить сигнал на оценку. False — ИИ выключен, нет ни снимка, ни метки времени,
    очередь полна или (для невзятого сигнала) нет резерва бюджета.

    fate — судьба сигнала из журнала (SENT/BLOCKED/SKIPPED); модели она не сообщается,
    мнение сверяется с исходом по свечам. signal_ts — метка журнала, если снимка нет.
    """
    if stage() == "off" or (snapshot is None and signal_ts is None):
        return False
    if fate != SENT and not _room_for_unsent():
        return False
    job = {
        "symbol": symbol,
        "signal_ts": signal_ts or snapshot.timestamp.isoformat(),
        "signal_data": dict(signal_data),
        "snapshot": snapshot,
        "fate": fate,
    }
    _ensure_thread()
    try:
        _queue.put_nowait(job)
        return True
    except queue.Full:
        logger.info("ai_trader: очередь полна — сигнал %s остался без мнения", symbol)
        return False


def _ensure_thread() -> None:
    global _thread
    with _thread_lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="AITraderReview", daemon=True)
            _thread.start()


def _run() -> None:
    while True:
        job = _queue.get()
        try:
            process(job)
        except Exception:
            logger.warning("ai_trader: оценка сигнала %s не удалась", job.get("symbol"), exc_info=True)
        finally:
            _queue.task_done()


def format_disagreement(job: dict, opinion) -> str:
    side = job["signal_data"].get("side", "")
    verdict = "отклонил бы" if opinion.decision == "reject" else f"уменьшил бы до {opinion.size_multiplier:.0%}"
    lines = [f"🤖 ИИ (тень): {job['symbol']} {side} — {verdict}, уверенность {opinion.confidence:.2f}"]
    lines += [f"• {reason}" for reason in opinion.reasons]
    if opinion.key_risk:
        lines.append(f"Главный риск: {opinion.key_risk}")
    lines.append("На сделку это не влияет — этап 0, мнения сверяются с исходами.")
    return "\n".join(lines)


def _notify(text: str) -> None:
    from core.system_guardian import AsyncToSyncAdapter
    from telegram_bot import send_message_async
    AsyncToSyncAdapter.call_async(send_message_async(text, parse_mode=None), timeout=15.0)


def process(job: dict, transport=None, notify: Optional[Callable[[str], None]] = None):
    """Оценить один сигнал: запрос, разбор, запись в базу, сообщение при несогласии. Возвращает мнение или None."""
    from ai_trader import client, prompts, review
    import database

    context = review.build_context(job["symbol"], job["signal_data"], job["snapshot"])
    completion = client.complete("review", prompts.REVIEW_SYSTEM, review.render(context),
                                 client.review_model(), max_tokens=REVIEW_MAX_TOKENS, transport=transport)
    opinion = review.parse_opinion(completion.text) if completion else None
    if completion is None:
        error = "no_response"
    elif opinion is None:
        # Обрыв по max_tokens — отдельно: это не «модель ответила не по схеме».
        error = "truncated" if completion.finish_reason == "length" else "bad_format"
    else:
        error = None
    database.save_ai_opinion(
        signal_ts=job["signal_ts"], symbol=job["symbol"], side=job["signal_data"].get("side"),
        stage=stage(), model=completion.model if completion else None,
        decision=opinion.decision if opinion else None,
        size_multiplier=opinion.size_multiplier if opinion else None,
        confidence=opinion.confidence if opinion else None,
        reasons=opinion.reasons if opinion else [], key_risk=opinion.key_risk if opinion else "",
        cost_usd=completion.cost_usd if completion else 0.0,
        latency_ms=completion.latency_ms if completion else None, error=error,
    )
    # Несогласие — только по взятому сигналу: по невзятому сделки нет, мнение идёт в статистику.
    if opinion is not None and opinion.decision != "approve" and job.get("fate", SENT) == SENT:
        try:
            (notify or _notify)(format_disagreement(job, opinion))
        except Exception:
            logger.warning("ai_trader: сообщение о несогласии по %s не отправлено", job["symbol"], exc_info=True)
    return opinion
