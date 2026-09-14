"""
Чистая логика И14: целевые веса из сигналов И4 и И3, ордера до цели, окно ребалансировки,
стоп по просадке. Без обращения к бирже — проверяется тестами на числах.
"""
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Mapping, Optional, Sequence

from backtest import momentum_xs as mx
from backtest import trend_ts as tt
from execution.order_executor import round_qty

# Множители ног из правила И14: равный риск (И4 0,665 : И3 0,335) × 3,02 под риск 40 % годовых.
K_TREND = 2.01
K_MOMENTUM = 1.01
TREND_LOOKBACK_D = 30
MOMENTUM_LOOKBACK_D = 28
LEVERAGE = 5
MAX_DRAWDOWN = 0.25                 # доля капитала — стоп по правилу
MIN_ORDER_FRACTION = 0.002          # разница меньше 0,2 % капитала (и не меньше MIN_ORDER_FLOOR) — не торгуется
MIN_ORDER_FLOOR = 5.0


def min_order_usdt(capital: float) -> float:
    return max(MIN_ORDER_FLOOR, MIN_ORDER_FRACTION * capital)
REBALANCE_DELAY_MS = 2 * 60_000     # ордера через 2 минуты после 00:00 UTC понедельника
WEEK_MS = mx.WEEK_MS


@dataclass
class Order:
    symbol: str
    side: str          # Buy | Sell
    qty: Decimal
    reduce_only: bool


def combined_weights(data: Mapping[str, dict], trend_symbols: Sequence[str], momentum_symbols: Sequence[str],
                     t: int) -> Dict[str, float]:
    """
    Вес монеты в долях капитала: K_TREND × вес И4 + K_MOMENTUM × вес И3. t_next = t — у И4 он
    нужен только для проверки цены выхода, которой вживую ещё нет.
    """
    weights: Dict[str, float] = {}
    for s, w in tt.targets(data, trend_symbols, t, t, TREND_LOOKBACK_D).items():
        weights[s] = weights.get(s, 0.0) + K_TREND * w
    pick = mx.baskets(data, momentum_symbols, t, MOMENTUM_LOOKBACK_D)
    if pick is not None:
        for s in pick[0]:
            weights[s] = weights.get(s, 0.0) + K_MOMENTUM * mx.WEIGHT
        for s in pick[1]:
            weights[s] = weights.get(s, 0.0) - K_MOMENTUM * mx.WEIGHT
    return {s: w for s, w in weights.items() if abs(w) > 1e-12}


def orders_to_target(targets_usdt: Mapping[str, float], positions_usdt: Mapping[str, float],
                     marks: Mapping[str, float], filters: Mapping[str, object], min_order: float) -> List[Order]:
    """
    Ордера, доводящие позиции (подписанный номинал, USDT) до целей. Цель 0 — закрытие
    reduce_only; разница меньше min_order или минимума биржи — пропуск.
    """
    out: List[Order] = []
    for s in sorted(set(targets_usdt) | set(positions_usdt)):
        target, have = targets_usdt.get(s, 0.0), positions_usdt.get(s, 0.0)
        delta = target - have
        mark, f = marks.get(s), filters.get(s)
        if not mark or f is None or abs(delta) < max(min_order, float(getattr(f, "min_notional", 0) or 0)):
            continue
        qty, err = round_qty(abs(delta) / mark, f)
        if err or qty <= 0:
            continue
        out.append(Order(s, "Buy" if delta > 0 else "Sell", qty, reduce_only=(abs(target) < 1e-9)))
    return out


def next_rebalance(now_ms: int) -> int:
    """Ближайший понедельник 00:00 UTC строго после now_ms."""
    return mx.mondays(now_ms + 1, now_ms + 1 + WEEK_MS)[0]


def due_rebalance(now_ms: int, last_done_t: Optional[int]) -> Optional[int]:
    """
    Момент t понедельника, чью ребалансировку пора выполнить: последний понедельник ≤ now, если
    прошло ≥ REBALANCE_DELAY_MS, тот же день (UTC) ещё идёт и она ещё не сделана. Иначе None.
    """
    d = datetime.fromtimestamp(now_ms / 1000, UTC)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    t = int(monday.timestamp() * 1000)
    if now_ms - t < REBALANCE_DELAY_MS or now_ms - t >= mx.DAY_MS or (last_done_t is not None and last_done_t >= t):
        return None
    return t


def drawdown_halt(peak_equity: float, equity: float, capital: float) -> bool:
    return peak_equity - equity > MAX_DRAWDOWN * capital
