"""
Bybit Market Data: Open Interest и Funding Rate.
Используются бесплатные публичные endpoints (без API key).
"""
import logging
import time
from typing import List, Dict, Optional
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.bybit.com"
TIMEOUT = 10

# Кэш: {symbol: (timestamp, data)} — TTL 5 минут
_oi_cache: Dict[str, tuple] = {}
_funding_cache: Dict[str, tuple] = {}
CACHE_TTL = 300  # 5 минут


def _get_cached(cache: dict, key: str) -> Optional[list]:
    """Возвращает данные из кэша если не устарели."""
    if key in cache:
        ts, data = cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def get_open_interest(symbol: str, interval: str = "5min", limit: int = 50) -> List[Dict]:
    """
    Получает историю Open Interest с Bybit.
    /v5/market/open-interest — бесплатный endpoint.

    Returns:
        list of dicts: [{"openInterest": "12345.67", "timestamp": "1234567890000"}, ...]
    """
    cached = _get_cached(_oi_cache, symbol)
    if cached is not None:
        return cached

    try:
        r = requests.get(
            f"{BASE_URL}/v5/market/open-interest",
            params={
                "category": "linear",
                "symbol": symbol,
                "intervalTime": interval,
                "limit": limit,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("result", {}).get("list", [])
        _oi_cache[symbol] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug("Failed to fetch OI for %s: %s", symbol, e)
        return []


def get_funding_rate(symbol: str, limit: int = 10) -> List[Dict]:
    """
    Получает историю Funding Rate с Bybit.
    /v5/market/funding/history — бесплатный endpoint.

    Returns:
        list of dicts: [{"fundingRate": "0.0001", "fundingRateTimestamp": "..."}, ...]
    """
    cached = _get_cached(_funding_cache, symbol)
    if cached is not None:
        return cached

    try:
        r = requests.get(
            f"{BASE_URL}/v5/market/funding/history",
            params={
                "category": "linear",
                "symbol": symbol,
                "limit": limit,
            },
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        result = data.get("result", {}).get("list", [])
        _funding_cache[symbol] = (time.time(), result)
        return result
    except Exception as e:
        logger.debug("Failed to fetch funding rate for %s: %s", symbol, e)
        return []
