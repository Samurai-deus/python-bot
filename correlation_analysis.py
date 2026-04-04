"""
Модуль для анализа корреляций между торговыми парами
"""
from itertools import combinations
from typing import Dict, List, Tuple


def calculate_correlation(candles1: List, candles2: List, period: int = 60) -> float:
    """
    Рассчитывает корреляцию Пирсона между двумя парами.
    
    Returns:
        float: Коэффициент корреляции от -1 до 1
    """
    if len(candles1) < period or len(candles2) < period:
        return 0.0
    
    # Берем последние period свечей
    closes1 = [float(c[4]) for c in candles1[-period:]]
    closes2 = [float(c[4]) for c in candles2[-period:]]
    
    # Нормализуем (процентные изменения)
    changes1 = []
    changes2 = []
    
    for i in range(1, len(closes1)):
        if closes1[i-1] > 0:
            changes1.append((closes1[i] - closes1[i-1]) / closes1[i-1])
        else:
            changes1.append(0)
        
        if i < len(closes2) and closes2[i-1] > 0:
            changes2.append((closes2[i] - closes2[i-1]) / closes2[i-1])
        else:
            changes2.append(0)
    
    if len(changes1) != len(changes2) or len(changes1) < 2:
        return 0.0
    
    # Средние значения
    mean1 = sum(changes1) / len(changes1)
    mean2 = sum(changes2) / len(changes2)
    
    # Ковариация и стандартные отклонения
    covariance = sum((changes1[i] - mean1) * (changes2[i] - mean2) 
                     for i in range(len(changes1))) / len(changes1)
    
    std1 = (sum((x - mean1) ** 2 for x in changes1) / len(changes1)) ** 0.5
    std2 = (sum((x - mean2) ** 2 for x in changes2) / len(changes2)) ** 0.5
    
    if std1 == 0 or std2 == 0:
        return 0.0
    
    # Корреляция Пирсона: cov / (std1 * std2)
    correlation = covariance / (std1 * std2)
    
    # Ограничиваем значение от -1 до 1
    correlation = max(-1.0, min(1.0, correlation))
    
    return correlation


def analyze_market_correlations(symbols: List[str], candles_map: Dict[str, Dict[str, List]], 
                                 timeframe: str = "15m") -> Dict[str, Dict]:
    """
    Анализирует корреляции между всеми парами.
    
    Returns:
        dict: {
            symbol: {
                "strong_correlations": [(symbol, corr), ...],
                "weak_correlations": [(symbol, corr), ...],
                "market_alignment": "HIGH"/"MEDIUM"/"LOW"
            }
        }
    """
    results = {}
    
    # Базовые пары для сравнения (BTC и ETH)
    base_symbols = ["BTCUSDT", "ETHUSDT"]
    
    for symbol in symbols:
        if symbol not in candles_map or timeframe not in candles_map[symbol]:
            continue
        
        symbol_candles = candles_map[symbol][timeframe]
        if len(symbol_candles) < 20:
            continue
        
        correlations = []
        
        # Сравниваем с базовыми парами
        for base_symbol in base_symbols:
            if base_symbol == symbol:
                continue
            if base_symbol not in candles_map or timeframe not in candles_map[base_symbol]:
                continue
            
            base_candles = candles_map[base_symbol][timeframe]
            if len(base_candles) < 20:
                continue
            
            corr = calculate_correlation(symbol_candles, base_candles, period=60)
            correlations.append((base_symbol, corr))
        
        # Сортируем по силе корреляции
        strong_correlations = [(s, c) for s, c in correlations if abs(c) > 0.7]
        weak_correlations = [(s, c) for s, c in correlations if 0.3 < abs(c) <= 0.7]
        
        # Определяем выравнивание рынка
        avg_corr = sum(abs(c) for _, c in correlations) / len(correlations) if correlations else 0
        
        if avg_corr > 0.7:
            market_alignment = "HIGH"
        elif avg_corr > 0.5:
            market_alignment = "MEDIUM"
        else:
            market_alignment = "LOW"
        
        results[symbol] = {
            "strong_correlations": strong_correlations,
            "weak_correlations": weak_correlations,
            "market_alignment": market_alignment,
            "avg_correlation": avg_corr
        }
    
    return results


def compute_pairwise_correlations(symbols_data: dict, period: int = 60) -> dict:
    """Compute correlations between all pairs of given symbols.

    This is used by risk_exposure_brain to detect concentration risk
    among open positions (not just vs BTC/ETH).

    Args:
        symbols_data: {symbol: candles_list} for each symbol with open position
        period: number of candles to use (default 60 = 15 hours on 15m)

    Returns:
        {(sym1, sym2): correlation_float} for all pairs
    """
    result: Dict[Tuple[str, str], float] = {}
    syms = list(symbols_data.keys())
    for sym1, sym2 in combinations(syms, 2):
        candles1 = symbols_data[sym1]
        candles2 = symbols_data[sym2]
        if not candles1 or not candles2:
            result[(sym1, sym2)] = 0.0
            continue
        result[(sym1, sym2)] = calculate_correlation(candles1, candles2, period=period)
    return result


def get_correlation_score(correlation_data: Dict, symbol: str) -> Tuple[int, List[str]]:
    """
    Возвращает score на основе корреляций (0-10 баллов).
    
    Returns:
        tuple: (score, reasons)
    """
    score = 0
    reasons = []
    
    if symbol not in correlation_data:
        return 0, []
    
    data = correlation_data[symbol]
    market_alignment = data.get("market_alignment", "LOW")
    avg_corr = data.get("avg_correlation", 0)
    strong_corr = data.get("strong_correlations", [])
    
    # Высокая корреляция с рынком — halved because we can't confirm
    # BTC direction aligns with signal direction without direction data
    if market_alignment == "HIGH" and avg_corr > 0.7:
        score += 4
        reasons.append(f"Высокая корреляция с рынком ({avg_corr:.2f})")
    elif market_alignment == "MEDIUM":
        score += 3
        reasons.append(f"Умеренная корреляция с рынком ({avg_corr:.2f})")
    elif market_alignment == "LOW":
        score += 1
        reasons.append(f"Низкая корреляция с рынком ({avg_corr:.2f}) - независимое движение")

    # Сильные корреляции с BTC/ETH — halved (was +2, now +1)
    if strong_corr:
        btc_corr = next((c for s, c in strong_corr if "BTC" in s), None)
        if btc_corr and abs(btc_corr) > 0.8:
            score += 1
            reasons.append(f"Сильная корреляция с BTC ({btc_corr:.2f})")

    return min(5, score), reasons

