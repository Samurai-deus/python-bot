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


def submit(symbol: str, signal_data: dict, snapshot) -> bool:
    """Поставить сигнал на оценку. False — ИИ выключен, нет снимка или очередь полна."""
    if snapshot is None or stage() == "off":
        return False
    job = {
        "symbol": symbol,
        "signal_ts": snapshot.timestamp.isoformat(),
        "signal_data": dict(signal_data),
        "snapshot": snapshot,
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
                                 client.review_model(), transport=transport)
    opinion = review.parse_opinion(completion.text) if completion else None
    if completion is None:
        error = "no_response"
    elif opinion is None:
        error = "bad_format"
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
    if opinion is not None and opinion.decision != "approve":
        try:
            (notify or _notify)(format_disagreement(job, opinion))
        except Exception:
            logger.warning("ai_trader: сообщение о несогласии по %s не отправлено", job["symbol"], exc_info=True)
    return opinion
