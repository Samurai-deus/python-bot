"""
Ограничения биржи на размер ордера — для всех режимов, включая бумажный.

Зачем бумажной торговле лимиты реальной биржи. Бумажный счёт в 100 $ имеет смысл,
только если его сделки могли бы случиться на самом деле. Замер 10.09.2026 по 28
символам из config.SYMBOLS: минимальный ордер по номиналу — 5 $ почти у всех,
у SOL около 10 $, у ETH около 25 $, у BTC около 78 $ (0,001 BTC). Прежний расчёт
размера давал при 100 $ позицию 0,1–3 $ — ниже минимума по ВСЕМ символам. Биржа
отклонила бы каждый такой ордер, а бумажный режим его «исполнял» и считал по нему
PnL. Результаты такой симуляции ничего не говорят о реальной торговле.

Источник — публичный /v5/market/instruments-info: без ключей, работает в DRY_RUN и
PAPER так же, как в TESTNET/LIVE. Кэш на 6 часов: лимиты меняются редко, а
запрашивать их на каждый сигнал — лишний трафик и лишняя точка отказа. Если
обновить не удалось, берём устаревшее значение: оно почти наверняка верно. Если
значения нет вовсе — отказ: без лимитов нельзя сказать, примет ли биржа ордер.
"""
import logging
import threading
import time
from decimal import ROUND_DOWN, Decimal
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
TESTNET_INSTRUMENTS_URL = "https://api-testnet.bybit.com/v5/market/instruments-info"
CACHE_TTL_SECONDS = 6 * 3600

_cache: Dict[str, Tuple[float, dict]] = {}
_lock = threading.Lock()


def _instruments_url() -> str:
    """Лимиты с того же хоста, куда уйдёт ордер: у тестнета они свои."""
    from trading_mode import uses_testnet_endpoint
    return TESTNET_INSTRUMENTS_URL if uses_testnet_endpoint() else INSTRUMENTS_URL


def _fetch_from_bybit(symbol: str) -> Optional[dict]:
    import requests

    response = requests.get(
        _instruments_url(), params={"category": "linear", "symbol": symbol}, timeout=10,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("retCode") != 0:
        logger.warning("instrument_limits: %s — retCode=%s %s", symbol, data.get("retCode"), data.get("retMsg"))
        return None
    items = (data.get("result") or {}).get("list") or []
    if not items:
        return None
    lot = items[0].get("lotSizeFilter") or {}
    return {
        "min_qty": Decimal(str(lot["minOrderQty"])),
        "qty_step": Decimal(str(lot["qtyStep"])),
        "min_notional": Decimal(str(lot.get("minNotionalValue") or "0")),
        "status": items[0].get("status"),
    }


def get_limits(symbol: str, fetch: Optional[Callable[[str], Optional[dict]]] = None) -> Optional[dict]:
    """Лимиты инструмента: из кэша, свежие с биржи или устаревшие, если биржа не ответила."""
    now = time.time()
    with _lock:
        cached = _cache.get(symbol)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    try:
        limits = (fetch or _fetch_from_bybit)(symbol)
    except Exception as exc:
        logger.warning("instrument_limits: не удалось получить лимиты %s: %s", symbol, exc)
        limits = None

    if limits:
        with _lock:
            _cache[symbol] = (now, limits)
        return limits
    if cached:
        logger.info("instrument_limits: %s — биржа не ответила, беру значение из кэша", symbol)
        return cached[1]
    return None


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def min_order_violation(symbol: str, notional_usd: float, entry_price: Optional[float],
                        fetch: Optional[Callable[[str], Optional[dict]]] = None) -> Optional[str]:
    """
    Почему биржа не примет ордер такого размера — или None, если примет.

    Количество округляется ВНИЗ до шага лота, как это делает исполнитель: номинал
    чуть выше минимума может после округления оказаться ниже него.
    """
    try:
        price = Decimal(str(entry_price)) if entry_price else Decimal(0)
    except Exception:
        price = Decimal(0)
    if price <= 0:
        return "нет цены входа — количество для ордера не посчитать"

    limits = get_limits(symbol, fetch)
    if limits is None:
        return f"лимиты инструмента {symbol} недоступны — нельзя проверить, примет ли биржа ордер"

    status = limits.get("status")
    if status and status != "Trading":
        return f"{symbol} сейчас не торгуется на бирже (status={status})"

    step = limits["qty_step"]
    raw_qty = Decimal(str(notional_usd)) / price
    qty = (raw_qty / step).to_integral_value(rounding=ROUND_DOWN) * step
    min_usd = max(limits["min_qty"] * price, limits["min_notional"])

    if qty < limits["min_qty"] or qty * price < limits["min_notional"]:
        return (
            f"ниже минимального ордера биржи: {float(notional_usd):.2f} $ при минимуме "
            f"{float(min_usd):.2f} $ для {symbol}"
        )
    return None
