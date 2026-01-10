import requests

BASE_URL = "https://api.bybit.com/v5/market/kline"

def get_candles(symbol, interval, limit=120):
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    r = requests.get(BASE_URL, params=params, timeout=10)
    r.raise_for_status()

    data = r.json()["result"]["list"]

    # Bybit отдаёт от новых к старым → разворачиваем
    return list(reversed(data))
