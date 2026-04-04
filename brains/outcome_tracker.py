"""
Outcome Tracker — маркирует прошлые сигналы результатами.

Для каждого ENTER-сигнала с entry/tp/sl загружает 1h свечи с Bybit
и определяет что случилось раньше: TP (WIN) или SL (LOSS).
Если ни одно не случилось за MAX_CANDLES свечей → NEUTRAL.
Сохраняет результат в таблицу signal_outcomes.

Принципы:
- НЕ влияет на торговую логику
- Только читает CSV и пишет в свою таблицу
- Использует синхронный requests (вызывается через asyncio.to_thread)
"""
import logging
import time
from datetime import datetime, UTC, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
_session: Optional[requests.Session] = None

MAX_CANDLES = 24       # Максимум свечей для проверки (24h при интервале 1h)
MIN_AGE_HOURS = 4      # Сигнал должен быть хотя бы 4 часа назад
MAX_AGE_DAYS = 14      # Не старше 14 дней
CANDLE_INTERVAL = "60" # 1h свечи


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"Accept": "application/json"})
    return _session


def _fetch_candles_since(symbol: str, start_ms: int, limit: int = 26) -> list:
    """
    Загружает свечи начиная с конкретного времени (UTC ms).

    Returns:
        list of candles [ts, open, high, low, close, ...], oldest first.
        Пустой список при ошибке.
    """
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": CANDLE_INTERVAL,
        "start": start_ms,
        "limit": limit,
    }
    for attempt in range(3):
        try:
            r = _get_session().get(_BYBIT_KLINE_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
            if data.get("retCode") != 0:
                logger.warning(
                    "[OutcomeTracker] Bybit error for %s: %s",
                    symbol, data.get("retMsg"),
                )
                return []
            candles = data.get("result", {}).get("list", [])
            return list(reversed(candles))  # Oldest first
        except Exception as e:
            if attempt < 2:
                time.sleep(2 ** attempt)
            else:
                logger.warning(
                    "[OutcomeTracker] Failed to fetch candles for %s: %s", symbol, e
                )
    return []


def _determine_outcome(
    direction: str,
    entry: float,
    tp: float,
    sl: float,
    candles: list,
) -> tuple:
    """
    Определяет исход сигнала, проходя по свечам.

    Логика для LONG:
      - high >= tp  → WIN
      - low  <= sl  → LOSS  (SL проверяется первым — консервативно)
    Логика для SHORT:
      - low  <= tp  → WIN
      - high >= sl  → LOSS

    Returns:
        (outcome: str, candles_checked: int,
         max_favorable_pct: float, max_adverse_pct: float)
    """
    is_long = direction.upper() == "LONG"
    max_favorable = 0.0
    max_adverse = 0.0

    for i, candle in enumerate(candles):
        try:
            open_price = float(candle[1])
            high = float(candle[2])
            low = float(candle[3])
        except (IndexError, ValueError, TypeError):
            continue

        if is_long:
            fav = (high - entry) / entry * 100
            adv = (entry - low) / entry * 100
            max_favorable = max(max_favorable, fav)
            max_adverse = max(max_adverse, adv)

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl and hit_tp:
                # Both SL and TP hit in same candle — use proximity to open
                dist_to_sl = abs(open_price - sl)
                dist_to_tp = abs(open_price - tp)
                if dist_to_sl < dist_to_tp:
                    return "LOSS", i + 1, max_favorable, max_adverse
                else:
                    return "WIN", i + 1, max_favorable, max_adverse
            elif hit_sl:
                return "LOSS", i + 1, max_favorable, max_adverse
            elif hit_tp:
                return "WIN", i + 1, max_favorable, max_adverse
        else:  # SHORT
            fav = (entry - low) / entry * 100
            adv = (high - entry) / entry * 100
            max_favorable = max(max_favorable, fav)
            max_adverse = max(max_adverse, adv)

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl and hit_tp:
                # Both SL and TP hit in same candle — use proximity to open
                dist_to_sl = abs(open_price - sl)
                dist_to_tp = abs(open_price - tp)
                if dist_to_sl < dist_to_tp:
                    return "LOSS", i + 1, max_favorable, max_adverse
                else:
                    return "WIN", i + 1, max_favorable, max_adverse
            elif hit_sl:
                return "LOSS", i + 1, max_favorable, max_adverse
            elif hit_tp:
                return "WIN", i + 1, max_favorable, max_adverse

    return "NEUTRAL", len(candles), max_favorable, max_adverse


def run_outcome_check() -> int:
    """
    Проверяет неразмеченные ENTER-сигналы и записывает их исходы.

    Читает CSV, фильтрует ENTER + entry/tp/sl + правильный возраст,
    проверяет каждый неразмеченный сигнал через Bybit API.

    Returns:
        Количество новых записанных исходов.
    """
    from journal import get_recent_signals
    from database import is_outcome_tracked, save_signal_outcome

    now = datetime.now(UTC)
    since = now - timedelta(days=MAX_AGE_DAYS)
    before = now - timedelta(hours=MIN_AGE_HOURS)

    try:
        all_signals = get_recent_signals(since=since)
    except Exception as e:
        logger.error("[OutcomeTracker] Failed to read signals: %s", e)
        return 0

    # Filter: ENTER + full geometry + old enough
    candidates = []
    for sig in all_signals:
        if sig.get("decision") != "ENTER":
            continue
        if not sig.get("entry") or not sig.get("tp") or not sig.get("sl"):
            continue
        direction = sig.get("direction", "")
        if direction not in ("LONG", "SHORT"):
            continue
        ts = sig.get("timestamp")
        if ts is None or ts >= before:
            continue
        candidates.append(sig)

    logger.info("[OutcomeTracker] %d ENTER signals to evaluate", len(candidates))

    newly_marked = 0
    for sig in candidates:
        signal_ts = sig["timestamp"].isoformat()
        symbol = sig["symbol"]

        try:
            if is_outcome_tracked(signal_ts, symbol):
                continue
        except Exception as e:
            logger.warning(
                "[OutcomeTracker] DB check failed for %s %s: %s", symbol, signal_ts, e
            )
            continue

        entry = float(sig["entry"])
        tp = float(sig["tp"])
        sl = float(sig["sl"])
        direction = sig["direction"]

        start_ms = int(sig["timestamp"].timestamp() * 1000)
        candles = _fetch_candles_since(symbol, start_ms, limit=MAX_CANDLES + 2)

        if not candles:
            logger.debug(
                "[OutcomeTracker] No candles for %s @ %s", symbol, signal_ts[:16]
            )
            continue

        # Skip first candle — it's the signal candle (may be partial at signal time)
        check_candles = candles[1:] if len(candles) > 1 else candles

        outcome, candles_checked, max_fav, max_adv = _determine_outcome(
            direction, entry, tp, sl, check_candles
        )

        outcome_data = {
            "signal_ts": signal_ts,
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "confidence": sig.get("confidence"),
            "state_15m": sig.get("states", {}).get("15m") if sig.get("states") else None,
            "checked_at": now.isoformat(),
            "outcome": outcome,
            "candles_checked": candles_checked,
            "max_favorable_pct": max_fav,
            "max_adverse_pct": max_adv,
        }

        try:
            saved_id = save_signal_outcome(outcome_data)
            if saved_id is not None:
                newly_marked += 1
                logger.debug(
                    "[OutcomeTracker] %s %s %s → %s (fav=%.1f%% adv=%.1f%%)",
                    symbol, direction, signal_ts[:16],
                    outcome, max_fav or 0.0, max_adv or 0.0,
                )
        except Exception as e:
            logger.warning(
                "[OutcomeTracker] Save failed for %s %s: %s", symbol, signal_ts, e
            )

    logger.info("[OutcomeTracker] Marked %d new outcomes", newly_marked)
    return newly_marked
