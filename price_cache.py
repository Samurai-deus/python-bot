"""
Shared in-memory cache of latest market prices.
Updated by signal_generator every analysis cycle (~5 min).
Read by WS snapshot builder to populate current_price for open positions.
"""
import threading

_lock = threading.Lock()
_prices: dict[str, float] = {}


def update(symbol: str, price: float) -> None:
    """Set latest price for a symbol."""
    with _lock:
        _prices[symbol] = price


def get(symbol: str) -> float | None:
    """Return latest cached price for a symbol, or None if not yet seen."""
    with _lock:
        return _prices.get(symbol)


def snapshot() -> dict[str, float]:
    """Return a copy of the full price dict."""
    with _lock:
        return dict(_prices)
