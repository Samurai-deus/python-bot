"""
Журнал сигналов: каждый сигнал-кандидат и его судьба (шаг 3 плана обучения, 11.09.2026).

Кандидат — сетап с направлением, входом, стопом и целью. Журнал пишет, что с ним стало:
  SENT    — ушёл в торговлю (гейткипер пропустил);
  BLOCKED — отказал гейткипер (Risk Core, мета-мозг, портфель, размер...), с причиной;
  SKIPPED — отсёк сам генератор (портфель заполнен, фандинг, предел за оборот...).
Outcome tracker размечает по свечам ВСЕ записи, и видно, не отсекают ли фильтры
прибыльные сигналы, а не только как отработали взятые.

До 11.09.2026 в режиме SQLite журнал писался в signals_log.csv внутри контейнера: файл
пропадал с каждым деплоем, в него попадали только отправленные сигналы, а outcome
tracker читал тот же пустой файл («0 ENTER signals to evaluate»). Теперь — только БД.
"""
import logging
import threading
import time
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence

from core import release
from core.signal_snapshot import SignalSnapshot

logger = logging.getLogger(__name__)

SENT = "SENT"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"

# Отсев до проверки новизны повторяется каждый оборот (≈ 5 мин), пока сетап жив. Такие
# записи вызывающий помечает collapse_repeats: одна на символ, сторону, судьбу и причину
# в час — иначе журнал и разметка исходов тонут в копиях одного сигнала.
REPEAT_WINDOW_SECONDS = 3600
_recent: Dict[tuple, float] = {}
_recent_lock = threading.Lock()


def _is_repeat(key: tuple, now: float) -> bool:
    with _recent_lock:
        last = _recent.get(key)
        if last is not None and now - last < REPEAT_WINDOW_SECONDS:
            return True
        _recent[key] = now
        if len(_recent) > 5000:
            for stale in [k for k, t in _recent.items() if now - t >= REPEAT_WINDOW_SECONDS]:
                del _recent[stale]
        return False


def _text(value) -> Optional[str]:
    """MarketState, RiskLevel, SignalDecision или строка — в строку для БД."""
    if value is None:
        return None
    return str(getattr(value, "value", value))


def record_signal(*, symbol: str, side: Optional[str], entry: Optional[float], stop: Optional[float],
                  target: Optional[float], status: str, reason_code: Optional[str] = None,
                  reason: Optional[str] = None, states: Optional[Dict] = None, risk=None,
                  score: Optional[float] = None, strategy: Optional[str] = None, decision=None,
                  confidence: Optional[float] = None, timestamp: Optional[datetime] = None,
                  collapse_repeats: bool = False) -> bool:
    """
    Записать сигнал-кандидат. True — записан; False — повтор в окне или сбой записи.
    Журнал на торговлю не влияет: сбой записи — предупреждение в лог, не исключение.
    """
    direction = side or ("LONG" if entry and target and target > entry else "SHORT" if entry and target else "")
    if collapse_repeats and _is_repeat((symbol, direction, status, reason_code), time.monotonic()):
        return False
    states = states or {}
    rr = abs(target - entry) / abs(entry - stop) if entry and stop and target and entry != stop else None
    row = {
        "timestamp": (timestamp or datetime.now(UTC)).isoformat(),
        "symbol": symbol,
        "state_1h": _text(states.get("1h")),
        "state_30m": _text(states.get("30m")),
        "state_15m": _text(states.get("15m")),
        "state_5m": _text(states.get("5m")),
        "risk": _text(risk),
        "entry": entry,
        "tp": target,
        "sl": stop,
        "rr_ratio": rr,
        "decision": _text(decision),
        "confidence": confidence,
        "direction": direction,
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "strategy": strategy,
        "score": score,
        "version": release.version(),
    }
    try:
        from database import log_signal_to_db
        log_signal_to_db(row)
        return True
    except Exception as e:
        logger.warning("Журнал сигналов: %s %s не записан: %s: %s", symbol, status, type(e).__name__, e)
        return False


def log_signal_snapshot(snapshot: SignalSnapshot, status: str = SENT, reason_code: Optional[str] = None,
                        reason: Optional[str] = None, strategy: Optional[str] = None) -> bool:
    """
    Записать сигнал по SignalSnapshot. Метка времени — snapshot.timestamp: по ней же
    ИИ-трейдер пишет мнение (ai_opinions) и outcome tracker — исход (signal_outcomes).
    Fault injection проверяется в SignalSnapshotStore.save() — точке входа.
    """
    return record_signal(
        symbol=snapshot.symbol, side=snapshot.side, entry=snapshot.entry, stop=snapshot.sl,
        target=snapshot.tp, status=status, reason_code=reason_code, reason=reason,
        states=snapshot.states, risk=snapshot.risk_level, score=snapshot.score, strategy=strategy,
        decision=snapshot.decision, confidence=snapshot.confidence, timestamp=snapshot.timestamp,
    )


def _parse_timestamp(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        moment = value
    else:
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def get_recent_signals(since: Optional[datetime] = None,
                       statuses: Optional[Sequence[str]] = None) -> List[Dict]:
    """
    Недавние сигналы из журнала, новые первыми.

    Args:
        since: начало периода (по умолчанию — вся история)
        statuses: только эти судьбы (SENT/BLOCKED/SKIPPED); None — все
    """
    from database import get_signals_from_db

    since_iso = since.isoformat() if since else "1970-01-01T00:00:00"
    result = []
    for r in get_signals_from_db(since_iso, limit=200, statuses=statuses):
        signal_time = _parse_timestamp(r.get("timestamp", ""))
        if signal_time is None:
            continue
        result.append({
            "timestamp": signal_time,
            "symbol": r.get("symbol", ""),
            "states": {
                "1h": r.get("state_1h"),
                "30m": r.get("state_30m"),
                "15m": r.get("state_15m"),
                "5m": r.get("state_5m"),
            },
            "risk": r.get("risk"),
            "decision": r.get("decision", ""),
            "confidence": r.get("confidence"),
            "direction": r.get("direction", ""),
            "entry": r.get("entry"),
            "tp": r.get("tp"),
            "sl": r.get("sl"),
            "status": r.get("status"),
            "reason_code": r.get("reason_code"),
            "reason": r.get("reason"),
            "strategy": r.get("strategy"),
        })
    return result
