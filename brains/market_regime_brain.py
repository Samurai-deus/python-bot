"""
Market Regime Brain - определяет режим рынка

Отвечает на вопрос: в каком рынке мы сейчас живём?
- trend / range
- high / low volatility  
- risk-on / risk-off
- macro pressure
"""
from typing import Dict, List, Optional
from core.decision_core import MarketRegime
from indicators import atr, adx
from states import market_direction, is_flat
from volatility_filter import calculate_volatility_metrics
from correlation_analysis import analyze_market_correlations


class MarketRegimeBrain:
    """
    Анализирует режим рынка и определяет текущее состояние.
    
    НЕ даёт сигналов, только анализирует контекст.
    """
    
    def __init__(self):
        """Инициализация brain (без состояния - состояние хранится в SystemState)"""
        pass
    
    def reset(self):
        """
        Сбрасывает состояние brain (теперь не нужно - состояние в SystemState).
        Оставлено для обратной совместимости.
        """
        pass
    
    def analyze(self, symbols: List[str], candles_map: Dict[str, Dict[str, List]], 
               system_state=None) -> MarketRegime:
        """
        Анализирует режим рынка на основе данных всех символов.
        
        Args:
            symbols: Список символов для анализа
            candles_map: Словарь свечей {symbol: {timeframe: [candles]}}
            
        Returns:
            MarketRegime: Состояние рынка
        """
        # 1. Определяем trend vs range
        trend_type = self._determine_trend_type(symbols, candles_map)
        
        # 2. Определяем уровень волатильности
        volatility_level = self._determine_volatility(symbols, candles_map)
        
        # 3. Определяем risk-on vs risk-off
        risk_sentiment = self._determine_risk_sentiment(symbols, candles_map)
        
        # 4. Определяем макро-давление (пока упрощенно)
        macro_pressure = self._determine_macro_pressure(symbols, candles_map)
        
        # 5. Рассчитываем уверенность
        confidence = self._calculate_confidence(
            trend_type, volatility_level, risk_sentiment, symbols, candles_map
        )
        
        regime = MarketRegime(
            trend_type=trend_type,
            volatility_level=volatility_level,
            risk_sentiment=risk_sentiment,
            macro_pressure=macro_pressure,
            confidence=confidence
        )
        
        # Обновляем состояние в SystemState (если передан)
        if system_state is not None:
            system_state.update_market_regime(regime)
        
        return regime
    
    def _determine_trend_type(self, symbols: List[str], 
                             candles_map: Dict[str, Dict[str, List]]) -> str:
        """
        Определяет тип рынка: TREND или RANGE
        
        Использует:
        - ADX для силы тренда
        - Направления на разных таймфреймах
        - Флэт-детектор
        """
        trend_scores = []
        range_scores = []
        
        for symbol in symbols[:5]:  # Анализируем топ-5 символов
            candles_15m = candles_map.get(symbol, {}).get("15m", [])
            candles_30m = candles_map.get(symbol, {}).get("30m", [])
            candles_4h = candles_map.get(symbol, {}).get("4h", [])
            
            if not candles_15m or not candles_30m:
                continue
            
            # Проверяем флэт
            atr_15m = atr(candles_15m)
            is_flat_market = is_flat(candles_15m, atr_15m)
            
            if is_flat_market:
                range_scores.append(1)
                continue
            
            # Проверяем силу тренда через ADX
            try:
                adx_data = adx(candles_30m, period=14)
                adx_strength = adx_data.get("strength", "WEAK")
                
                if adx_strength == "STRONG":
                    trend_scores.append(2)
                elif adx_strength == "MODERATE":
                    trend_scores.append(1)
                else:
                    range_scores.append(1)
            except Exception:
                pass
            
            # Проверяем согласованность направлений
            direction_4h = market_direction(candles_4h) if candles_4h else "FLAT"
            direction_30m = market_direction(candles_30m)
            direction_15m = market_direction(candles_15m)
            
            if direction_4h != "FLAT" and direction_4h == direction_30m == direction_15m:
                trend_scores.append(2)
            elif direction_30m == direction_15m and direction_30m != "FLAT":
                trend_scores.append(1)
            else:
                range_scores.append(1)
        
        # Принимаем решение
        total_trend = sum(trend_scores)
        total_range = sum(range_scores)
        
        if total_trend > total_range * 1.5:
            return "TREND"
        elif total_range > total_trend * 1.5:
            return "RANGE"
        else:
            return "RANGE"  # По умолчанию range, если неясно
    
    def _determine_volatility(self, symbols: List[str],
                             candles_map: Dict[str, Dict[str, List]]) -> str:
        """
        Определяет уровень волатильности: HIGH, MEDIUM, LOW
        """
        volatility_levels = []
        
        for symbol in symbols[:5]:  # Топ-5 символов
            candles_15m = candles_map.get(symbol, {}).get("15m", [])
            if not candles_15m:
                continue
            
            metrics = calculate_volatility_metrics(candles_15m)
            level = metrics.get("volatility_level", "NORMAL")
            
            if level == "EXTREME":
                volatility_levels.append("HIGH")
            elif level == "HIGH":
                volatility_levels.append("HIGH")
            elif level == "NORMAL":
                volatility_levels.append("MEDIUM")
            else:
                volatility_levels.append("LOW")
        
        if not volatility_levels:
            return "MEDIUM"
        
        # Подсчитываем
        high_count = volatility_levels.count("HIGH")
        low_count = volatility_levels.count("LOW")
        medium_count = volatility_levels.count("MEDIUM")
        
        if high_count > len(volatility_levels) * 0.5:
            return "HIGH"
        elif low_count > len(volatility_levels) * 0.5:
            return "LOW"
        else:
            return "MEDIUM"
    
    def _determine_risk_sentiment(self, symbols: List[str],
                                 candles_map: Dict[str, Dict[str, List]]) -> str:
        """
        Определяет risk-on vs risk-off
        
        Использует корреляции и поведение альткоинов vs BTC
        """
        # Упрощенная логика: если BTC растет и альткоины тоже - risk-on
        # Если BTC растет, а альткоины падают - risk-off
        
        btc_candles = candles_map.get("BTCUSDT", {}).get("15m", [])
        if not btc_candles or len(btc_candles) < 20:
            return "NEUTRAL"
        
        # Направление BTC
        btc_direction = market_direction(btc_candles)
        
        # Проверяем альткоины
        alt_symbols = [s for s in symbols if s != "BTCUSDT"][:5]
        alt_directions = []
        
        for symbol in alt_symbols:
            candles = candles_map.get(symbol, {}).get("15m", [])
            if candles:
                direction = market_direction(candles)
                alt_directions.append(direction)
        
        if not alt_directions:
            return "NEUTRAL"
        
        # Если BTC растет и большинство альтов тоже - risk-on
        if btc_direction == "UP":
            up_alts = sum(1 for d in alt_directions if d == "UP")
            if up_alts > len(alt_directions) * 0.6:
                return "RISK_ON"
            elif up_alts < len(alt_directions) * 0.3:
                return "RISK_OFF"
        
        # Если BTC падает и большинство альтов тоже - risk-off
        elif btc_direction == "DOWN":
            down_alts = sum(1 for d in alt_directions if d == "DOWN")
            if down_alts > len(alt_directions) * 0.6:
                return "RISK_OFF"
        
        return "NEUTRAL"
    
    def _determine_macro_pressure(self, symbols: List[str],
                                 candles_map: Dict[str, Dict[str, List]]) -> Optional[str]:
        """
        Определяет макро-давление
        
        Анализирует:
        - Синхронность движений всех активов (высокая = макро-давление)
        - Волатильность BTC как индикатор макро-настроений
        - Корреляции между активами
        """
        if "BTCUSDT" not in symbols or "BTCUSDT" not in candles_map:
            return None
        
        btc_candles = candles_map.get("BTCUSDT", {}).get("15m", [])
        if not btc_candles or len(btc_candles) < 20:
            return None
        
        # Анализируем волатильность BTC
        btc_metrics = calculate_volatility_metrics(btc_candles)
        btc_volatility = btc_metrics.get("volatility_level", "NORMAL")
        
        # Если BTC имеет очень высокую волатильность - макро-давление
        if btc_volatility == "EXTREME":
            return "HIGH"
        
        # Проверяем синхронность движений
        # Если большинство активов движутся в одном направлении - макро-давление
        directions_count = {"UP": 0, "DOWN": 0, "FLAT": 0}
        
        for symbol in symbols[:10]:  # Топ-10 символов
            candles = candles_map.get(symbol, {}).get("15m", [])
            if candles:
                direction = market_direction(candles)
                if direction in directions_count:
                    directions_count[direction] += 1
        
        total = sum(directions_count.values())
        if total > 0:
            max_direction_count = max(directions_count.values())
            sync_ratio = max_direction_count / total
            
            # Если >70% активов движутся синхронно - макро-давление
            if sync_ratio > 0.7:
                dominant_direction = max(directions_count, key=directions_count.get)
                if dominant_direction != "FLAT":
                    return "MEDIUM" if btc_volatility == "HIGH" else "LOW"
        
        return None
    
    def _calculate_confidence(self, trend_type: str, volatility_level: str,
                              risk_sentiment: str, symbols: List[str],
                              candles_map: Dict[str, Dict[str, List]]) -> float:
        """
        Рассчитывает уверенность в определении режима (0.0 - 1.0)
        """
        confidence = 0.5  # Базовая уверенность
        
        # Если есть четкий тренд - больше уверенности
        if trend_type == "TREND":
            confidence += 0.2
        
        # Если волатильность определена четко - больше уверенности
        if volatility_level in ["HIGH", "LOW"]:
            confidence += 0.1
        
        # Если risk sentiment четкий - больше уверенности
        if risk_sentiment != "NEUTRAL":
            confidence += 0.2
        
        return min(1.0, confidence)


# Глобальный экземпляр
_market_regime_brain = None

def get_market_regime_brain() -> MarketRegimeBrain:
    """Получить глобальный экземпляр Market Regime Brain"""
    global _market_regime_brain
    if _market_regime_brain is None:
        _market_regime_brain = MarketRegimeBrain()
    return _market_regime_brain

