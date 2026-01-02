"""
Модуль для анализа корреляций между торговыми парами
"""
from typing import Dict, List, Tuple


def calculate_correlation(candles1: List, candles2: List, period: int = 20) -> float:
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
            
            corr = calculate_correlation(symbol_candles, base_candles, period=20)
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
    
    # Высокая корреляция с рынком - хорошо для подтверждения тренда
    if market_alignment == "HIGH" and avg_corr > 0.7:
        score += 8
        reasons.append(f"Высокая корреляция с рынком ({avg_corr:.2f})")
    elif market_alignment == "MEDIUM":
        score += 5
        reasons.append(f"Умеренная корреляция с рынком ({avg_corr:.2f})")
    elif market_alignment == "LOW":
        score += 2
        reasons.append(f"Низкая корреляция с рынком ({avg_corr:.2f}) - независимое движение")
    
    # Сильные корреляции с BTC/ETH
    if strong_corr:
        btc_corr = next((c for s, c in strong_corr if "BTC" in s), None)
        if btc_corr and abs(btc_corr) > 0.8:
            score += 2
            reasons.append(f"Сильная корреляция с BTC ({btc_corr:.2f})")
    
    return min(10, score), reasons

