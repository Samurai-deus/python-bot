"""
Итоговый размер позиции: не больше одобренного Risk Core, не меньше минимума биржи.

Находка M6 из аудита. Размер считался дважды и по-разному. signal_generator
считал его от риска на сделку, Risk Core проверял именно это число, а через
двести строк гейткипера PositionSizer затирал его своим. В сделку уходило число,
которое проверку риска не проходило. Лимиты Risk Core на одну позицию и на всю
экспозицию считались по одному размеру, а открывался другой.

Правило здесь: Risk Core одобряет верхнюю границу. Всё, что после него уменьшает
размер (PortfolioBrain, режим ALLOW_LIMITED, PositionSizer), может только
уменьшать. Если PositionSizer насчитал больше одобренного — берём одобренное.

Второе правило — размер, который реальная биржа не примет, не открывается ни в
каком режиме (см. market_data.instrument_limits).
"""
import logging
from typing import Optional, Tuple

from market_data.instrument_limits import min_order_usd, min_order_violation

logger = logging.getLogger(__name__)


def _positive_price(source, key) -> Optional[float]:
    try:
        value = float(source.get(key) or 0)
    except (TypeError, ValueError, AttributeError):
        return None
    return value if value > 0 else None


def entry_price_from_signal(signal_data) -> Optional[float]:
    """
    Цена входа из сигнала. signal_generator кладёт её и на верхний уровень,
    и в signal_data["zone"]; Risk Core читает из zone, исполнитель — сверху.
    Берём верхний уровень, иначе zone.
    """
    if not signal_data:
        return None
    return _positive_price(signal_data, "entry") or _positive_price(signal_data.get("zone") or {}, "entry")


def stop_price_from_signal(signal_data) -> Optional[float]:
    if not signal_data:
        return None
    return _positive_price(signal_data, "stop") or _positive_price(signal_data.get("zone") or {}, "stop")


def stop_distance_from_signal(signal_data) -> Optional[float]:
    """Расстояние от входа до стопа как доля цены входа, или None, если не посчитать."""
    entry = entry_price_from_signal(signal_data)
    stop = stop_price_from_signal(signal_data)
    if entry is None or stop is None or entry == stop:
        return None
    return abs(entry - stop) / entry


def finalize_position_size(symbol: str, sized_usd: Optional[float], approved_usd: Optional[float],
                           entry_price: Optional[float], fetch=None) -> Tuple[Optional[float], Optional[str]]:
    """
    Возвращает (итоговый размер, None) или (None, причина отказа).

    sized_usd    — что насчитал PositionSizer;
    approved_usd — что к этому моменту лежит в signal_data["position_size"]:
                   одобренное Risk Core, возможно уже уменьшенное дальше по цепочке.
                   0/None — предварительного размера не было, берём насчитанное.
    """
    if not sized_usd or sized_usd <= 0:
        return None, "PositionSizer не дал положительного размера"

    final = float(sized_usd)
    if approved_usd and approved_usd > 0 and final > approved_usd:
        logger.info(
            "[SIZER] %s: размер %.2f $ урезан до одобренного Risk Core %.2f $",
            symbol, final, approved_usd,
        )
        final = float(approved_usd)

    violation = min_order_violation(symbol, final, entry_price, fetch=fetch)
    if violation:
        return None, violation
    return final, None


def unaffordable_reason(symbol: str, price: Optional[float], position_cap_usd: float,
                        fetch=None) -> Optional[str]:
    """
    Почему символ не кандидат для сделки — или None.

    Минимальный ордер биржи больше предела одной позиции: такую сделку не открыть
    ни при каком сигнале. При 100 $ и пределе 10 % это BTC (минимум около 77 $),
    ETH (около 24 $) и SOL (10,01 $). С ростом капитала символ возвращается сам.
    Нет цены, предела или лимитов биржи — None: решают проверки дальше по цепочке.
    """
    if not price or price <= 0 or position_cap_usd <= 0:
        return None
    min_usd = min_order_usd(symbol, price, fetch=fetch)
    if min_usd is None or min_usd <= position_cap_usd:
        return None
    return f"минимальный ордер биржи {min_usd:.2f} $ больше предела позиции {position_cap_usd:.2f} $"
