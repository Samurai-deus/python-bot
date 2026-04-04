"""
Shared in-memory cache of latest market prices.
Updated by signal_generator every analysis cycle (~5 min).
Read by WS snapshot builder to populate current_price for open positions.

Each entry stores (price, monotonic_timestamp) so consumers can detect stale data.
"""
import threading
import time

_lock = threading.Lock()
_prices: dict[str, tuple[float, float]] = {}


def set_price(symbol: str, price: float) -> None:
    """Set latest price for a symbol with current timestamp."""
    with _lock:
        _prices[symbol] = (price, time.monotonic())


def get_price(symbol: str, max_age: float = 120.0) -> float | None:
    """Get cached price. Returns None if stale (older than max_age seconds)."""
    with _lock:
        if symbol not in _prices:
            return None
        price, ts = _prices[symbol]
        if time.monotonic() - ts > max_age:
            return None
        return price


def get_price_unchecked(symbol: str) -> float:
    """Get cached price without staleness check (backward compat)."""
    with _lock:
        if symbol not in _prices:
            return 0.0
        val = _prices[symbol]
        if isinstance(val, tuple):
            return val[0]
        return val  # backward compat with plain float


# ── Backward-compatible API ──


def update(symbol: str, price: float) -> None:
    """Set latest price for a symbol (backward-compatible alias)."""
    set_price(symbol, price)


def get(symbol: str) -> float | None:
    """Return latest cached price for a symbol, or None if not yet seen.

    This is the backward-compatible version — no staleness check.
    Use get_price() with max_age for staleness-aware reads.
    """
    with _lock:
        if symbol not in _prices:
            return None
        val = _prices[symbol]
        if isinstance(val, tuple):
            return val[0]
        return val


def snapshot() -> dict[str, float]:
    """Return a copy of the full price dict (values only, no timestamps)."""
    with _lock:
        result = {}
        for sym, val in _prices.items():
            if isinstance(val, tuple):
                result[sym] = val[0]
            else:
                result[sym] = val
        return result
