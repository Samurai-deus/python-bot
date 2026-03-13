import requests  # type: ignore[import-untyped]  # noqa: F401
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Tuple, List, Optional

BASE_URL = "https://api.bybit.com/v5/market/kline"
INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"

def get_candles(symbol, interval, limit=120):
    """
    Получает свечи с Bybit API.
    
    Args:
        symbol: Торговая пара (например, "BTCUSDT")
        interval: Интервал свечей (например, "5", "15", "60")
        limit: Количество свечей для получения
    
    Returns:
        list: Список свечей, отсортированных от старых к новым
    """
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": interval,
        "limit": limit
    }

    try:
        r = requests.get(BASE_URL, params=params, timeout=10)
        r.raise_for_status()

        data = r.json()
        
        # Проверяем наличие ошибок в ответе API
        if "retCode" in data and data["retCode"] != 0:
            error_msg = data.get("retMsg", "Неизвестная ошибка API")
            logging.warning("API ошибка для %s (%s): %s (код: %s)", symbol, interval, error_msg, data['retCode'])
            return []
        
        if "result" not in data:
            logging.warning("Отсутствует 'result' в ответе API для %s (%s). Ответ: %s", symbol, interval, data)
            return []
        
        if "list" not in data["result"]:
            logging.warning(f"Отсутствует 'list' в ответе API для {symbol} ({interval}). Result: {data.get('result', {})}")
            return []
        
        candles = data["result"]["list"]
        if not candles:
            # Проверяем, может быть символ недоступен или переименован
            logging.warning("Пустой список свечей для %s (%s). Возможно, символ недоступен на Bybit или переименован.", symbol, interval)
            return []

        # Bybit отдаёт от новых к старым → разворачиваем
        return list(reversed(candles))
    
    except requests.exceptions.RequestException as e:
        logging.warning("Ошибка при получении свечей для %s (%s): %s", symbol, interval, e)
        return []
    except (KeyError, ValueError) as e:
        logging.warning("Ошибка парсинга данных для %s (%s): %s", symbol, interval, e)
        return []


def get_candles_parallel(symbols: List[str], timeframes: Dict[str, str], 
                         limit: int = 120, max_workers: int = 20) -> Dict[str, Dict[str, List]]:
    """
    Параллельно загружает свечи для всех символов и таймфреймов.
    
    Args:
        symbols: Список торговых пар (например, ["BTCUSDT", "ETHUSDT"])
        timeframes: Словарь таймфреймов {название: интервал} (например, {"15m": "15", "1h": "60"})
        limit: Количество свечей для получения
        max_workers: Максимальное количество потоков
    
    Returns:
        dict: Словарь вида {symbol: {timeframe: [candles]}}
        Например: {"BTCUSDT": {"15m": [...], "1h": [...]}}
    """
    result = {}
    
    # Создаём задачи для параллельного выполнения
    tasks = []
    for symbol in symbols:
        result[symbol] = {}
        for tf_name, tf_interval in timeframes.items():
            tasks.append((symbol, tf_name, tf_interval))
    
    # Выполняем задачи параллельно
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Создаём futures для всех задач
        futures = {
            executor.submit(get_candles, symbol, tf_interval, limit): (symbol, tf_name)
            for symbol, tf_name, tf_interval in tasks
        }
        
        # Собираем результаты
        for future in as_completed(futures):
            symbol, tf_name = futures[future]
            try:
                candles = future.result()
                result[symbol][tf_name] = candles
            except Exception as e:
                logging.warning("Ошибка при загрузке свечей для %s %s: %s", symbol, tf_name, e)
                result[symbol][tf_name] = []
    
    return result
