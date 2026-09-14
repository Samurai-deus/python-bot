"""
Чистая логика И13: размер пары, подгонка хеджа, опасная маржа. Без обращения к бирже — всё
проверяется тестами на числах.
"""
import math
from typing import Optional

HEDGE_TOLERANCE = 0.02            # подгонять шорт, если он отличается от спота больше чем на 2 %
DANGER_MM_RATE = 1 / 1.5          # маржа ≤ 150 % поддерживающей ⇔ accountMMRate ≥ 2/3
NEAR_HEDGE = 0.05                 # «хедж вне ±5 %» — для критерия времени


def floor_step(qty: float, step: float) -> float:
    """Округлить вниз до шага биржи (с защитой от 0.30000000000000004)."""
    if step <= 0:
        raise ValueError("шаг должен быть положительным")
    return round(math.floor(qty / step + 1e-9) * step, 12)


def pair_qty(notional_usdt: float, price: float, perp_step: float, perp_min: float) -> float:
    """Объём монеты на пару: номинал / цена, вниз до шага контракта; меньше минимума — 0."""
    if price <= 0:
        raise ValueError("цена должна быть положительной")
    qty = floor_step(notional_usdt / price, perp_step)
    return qty if qty >= perp_min else 0.0


def hedge_deviation(spot_qty: float, short_qty: float) -> float:
    """Относительное расхождение шорта и спота (0 — хедж точный); спота нет — бесконечность."""
    if spot_qty <= 0:
        return math.inf if short_qty > 0 else 0.0
    return abs(short_qty - spot_qty) / spot_qty


def hedge_adjustment(spot_qty: float, short_qty: float, perp_step: float,
                     tolerance: float = HEDGE_TOLERANCE) -> Optional[float]:
    """
    Сколько добавить к шорту (> 0 — продать ещё контракт, < 0 — откупить часть), чтобы он сравнялся
    со спотом; None — расхождение в допуске или меньше шага контракта.
    """
    if hedge_deviation(spot_qty, short_qty) <= tolerance:
        return None
    delta = floor_step(abs(spot_qty - short_qty), perp_step)
    if delta <= 0:
        return None
    return delta if spot_qty > short_qty else -delta


def margin_danger(account_mm_rate: Optional[float]) -> bool:
    """Опасная маржа: поддерживающая маржа ≥ 2/3 капитала счёта (запас меньше 150 %). Нет данных — не опасно."""
    return account_mm_rate is not None and account_mm_rate >= DANGER_MM_RATE
