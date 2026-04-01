"""
Управление демо-сделками: открытие, отслеживание, закрытие.

Multi-stage exit logic:
  1. Hard SL — закрытие при достижении стопа
  2. Breakeven — после +1R перенос стопа на вход + 0.1R буфер
  3. Partial TP — после +1.5R закрытие 50% позиции
  4. Trailing stop — после +1R тралим стоп на 0.5R за ценой
  5. Time exit — если >6ч и движение <0.3R, закрытие по рынку
  6. Full TP — закрытие остатка при достижении target
"""
import logging
from datetime import datetime, UTC
from typing import List, Dict

from database import (
    get_open_trades as db_get_open_trades,
    close_trade as db_close_trade,
    update_trade_stop,
    update_trade_partial,
)

logger = logging.getLogger(__name__)


def get_open_trades() -> List[Dict]:
    """Получает список всех открытых сделок из БД."""
    return db_get_open_trades()


def _calc_r_multiple(trade: Dict, current_price: float) -> float:
    """Текущий R-множитель: сколько R (risk units) в текущем движении."""
    entry = trade["entry"]
    # Используем ОРИГИНАЛЬНЫЙ стоп для расчёта risk distance,
    # а не текущий (который мог быть сдвинут trailing'ом)
    original_stop = trade.get("original_stop") or trade["stop"]
    risk_distance = abs(entry - original_stop)
    if risk_distance == 0:
        return 0.0

    if trade["side"] == "LONG":
        return (current_price - entry) / risk_distance
    else:
        return (entry - current_price) / risk_distance


def _get_trade_duration_minutes(trade: Dict) -> float:
    """Сколько минут прошло с открытия сделки."""
    try:
        opened = datetime.fromisoformat(trade["timestamp"])
        if opened.tzinfo is None:
            from datetime import timezone
            opened = opened.replace(tzinfo=timezone.utc)
        now = datetime.now(UTC)
        return (now - opened).total_seconds() / 60.0
    except Exception:
        return 0.0


def _calc_pnl(trade: Dict, close_price: float, fraction: float = 1.0) -> float:
    """PnL в USDT для доли позиции."""
    position_size = trade.get("position_size")
    if not position_size:
        from capital import MIN_POSITION_SIZE
        position_size = MIN_POSITION_SIZE

    # Если partial уже закрыт, остаток = 50%
    if trade.get("partial_closed") and fraction == 1.0:
        position_size *= 0.5

    if trade["side"] == "LONG":
        price_change_pct = (close_price - trade["entry"]) / trade["entry"]
    else:
        price_change_pct = (trade["entry"] - close_price) / trade["entry"]

    return price_change_pct * position_size * fraction


def check_trades(symbol: str, current_price: float) -> List[Dict]:
    """
    Проверяет открытые сделки для символа.
    Multi-stage exit: SL → breakeven → partial TP → trailing → time exit → full TP.

    Returns:
        list: Закрытые сделки с результатами
    """
    open_trades = get_open_trades()
    closed_trades = []

    for trade in open_trades:
        if trade["symbol"] != symbol:
            continue

        entry = trade["entry"]
        stop = trade["stop"]
        target = trade["target"]
        side = trade["side"]

        # Для R-расчёта используем ОРИГИНАЛЬНЫЙ стоп (сохранён при открытии сделки).
        # original_stop гарантирует правильный risk_distance независимо от
        # того, был ли стоп сдвинут breakeven/trailing.
        original_stop = trade.get("original_stop") or stop
        risk_distance = abs(entry - original_stop)

        if risk_distance == 0:
            risk_distance = entry * 0.003  # fallback 0.3%

        if side == "LONG":
            current_r = (current_price - entry) / risk_distance
        else:
            current_r = (entry - current_price) / risk_distance

        # --- Stage 1: Hard Stop Loss ---
        hit_sl = (side == "LONG" and current_price <= stop) or \
                 (side == "SHORT" and current_price >= stop)
        if hit_sl:
            pnl = _calc_pnl(trade, current_price)
            # Добавляем partial_pnl если была частичная фиксация
            total_pnl = pnl + (trade.get("partial_pnl") or 0.0)
            if db_close_trade(trade["id"], current_price, "STOP_LOSS", total_pnl):
                closed_trades.append({**trade, "close_price": current_price, "close_reason": "STOP_LOSS", "pnl": total_pnl})
            continue

        # --- Stage 2: Breakeven stop (после +1R) ---
        if current_r >= 1.0 and not trade.get("breakeven_set"):
            buffer = risk_distance * 0.1
            if side == "LONG":
                new_stop = entry + buffer
            else:
                new_stop = entry - buffer
            update_trade_stop(trade["id"], new_stop, breakeven=True)
            trade["stop"] = new_stop
            trade["breakeven_set"] = 1
            logger.info("Trade #%d %s: breakeven set @ %.4f (was %.4f)",
                        trade["id"], symbol, new_stop, stop)

        # --- Stage 3: Partial TP at 1.5R (50% позиции) ---
        if current_r >= 1.5 and not trade.get("partial_closed"):
            partial_pnl = _calc_pnl(trade, current_price, fraction=0.5)
            update_trade_partial(trade["id"], current_price, partial_pnl)
            trade["partial_closed"] = 1
            trade["partial_pnl"] = partial_pnl
            logger.info("Trade #%d %s: partial TP 50%% @ %.4f, PnL=%.2f (R=%.1f)",
                        trade["id"], symbol, current_price, partial_pnl, current_r)

        # --- Stage 4: Trailing stop (после +1R, тралим на 0.5R) ---
        if current_r >= 1.0:
            trail_distance = risk_distance * 0.5
            if side == "LONG":
                new_trailing = current_price - trail_distance
                current_stop = trade.get("trailing_stop") or trade["stop"]
                if new_trailing > current_stop:
                    update_trade_stop(trade["id"], new_trailing)
                    trade["stop"] = new_trailing
                    logger.debug("Trade #%d %s: trailing stop → %.4f (R=%.1f)",
                                 trade["id"], symbol, new_trailing, current_r)
            else:
                new_trailing = current_price + trail_distance
                current_stop = trade.get("trailing_stop") or trade["stop"]
                if new_trailing < current_stop:
                    update_trade_stop(trade["id"], new_trailing)
                    trade["stop"] = new_trailing
                    logger.debug("Trade #%d %s: trailing stop → %.4f (R=%.1f)",
                                 trade["id"], symbol, new_trailing, current_r)

        # --- Stage 5: Time exit (>6h с движением <0.3R) ---
        duration_min = _get_trade_duration_minutes(trade)
        if duration_min > 360 and abs(current_r) < 0.3:
            pnl = _calc_pnl(trade, current_price)
            total_pnl = pnl + (trade.get("partial_pnl") or 0.0)
            if db_close_trade(trade["id"], current_price, "TIME_EXIT", total_pnl):
                closed_trades.append({**trade, "close_price": current_price, "close_reason": "TIME_EXIT", "pnl": total_pnl})
                logger.info("Trade #%d %s: time exit after %.0fmin (R=%.2f), PnL=%.2f",
                            trade["id"], symbol, duration_min, current_r, total_pnl)
            continue

        # --- Stage 6: Full Take Profit ---
        hit_tp = (side == "LONG" and current_price >= target) or \
                 (side == "SHORT" and current_price <= target)
        if hit_tp:
            pnl = _calc_pnl(trade, current_price)
            total_pnl = pnl + (trade.get("partial_pnl") or 0.0)
            if db_close_trade(trade["id"], current_price, "TAKE_PROFIT", total_pnl):
                closed_trades.append({**trade, "close_price": current_price, "close_reason": "TAKE_PROFIT", "pnl": total_pnl})
            continue

    return closed_trades
