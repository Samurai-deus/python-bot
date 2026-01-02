"""
Opportunity Awareness Bot - готовит возможности, не толкает

Отслеживает:
- сжатие волатильности
- накопление
- расхождения ожидание / реакция
- подозрительная тишина
"""
from typing import Dict, List, Optional
from core.decision_core import Opportunity
from volatility_filter import calculate_volatility_metrics
from indicators import atr, bollinger_bands, volume_analysis, rsi
from states import is_flat
from datetime import datetime, UTC, timedelta
import hashlib
import json


class OpportunityAwareness:
    """
    Отслеживает возможности на рынке.
    
    НЕ даёт сигналов, только готовит контекст.
    """
    
    def __init__(self):
        # Кэш для результатов анализа
        self._cache: Dict[str, tuple] = {}  # {cache_key: (opportunity, timestamp)}
        self._cache_ttl = timedelta(minutes=5)  # Время жизни кэша - 5 минут
        # Явное состояние (последний проанализированный символ)
        self.state: Optional[Dict[str, Opportunity]] = {}  # {symbol: Opportunity}
    
    def _get_cache_key(self, symbol: str, candles_map: Dict[str, List]) -> str:
        """
        Генерирует ключ кэша на основе символа и последних свечей.
        
        Использует последние 5 свечей каждого таймфрейма для генерации ключа.
        """
        try:
            key_data = {"symbol": symbol}
            for tf, candles in candles_map.items():
                if candles and len(candles) >= 5:
                    # Берем последние 5 свечей (close prices)
                    last_5 = [float(c[4]) for c in candles[-5:]]
                    key_data[tf] = last_5
            
            # Создаем хэш от данных
            key_str = json.dumps(key_data, sort_keys=True)
            return hashlib.md5(key_str.encode()).hexdigest()
        except Exception:
            # Если ошибка - используем только символ
            return f"{symbol}_{datetime.now(UTC).timestamp()}"
    
    def _is_cache_valid(self, cache_entry: tuple) -> bool:
        """Проверяет, действителен ли кэш"""
        if not cache_entry:
            return False
        opportunity, timestamp = cache_entry
        return datetime.now(UTC) - timestamp < self._cache_ttl
    
    def analyze(self, symbol: str, candles_map: Dict[str, List], 
               system_state=None) -> Opportunity:
        """
        Анализирует возможности для символа.
        
        Использует кэширование для оптимизации производительности.
        
        Args:
            symbol: Торговая пара
            candles_map: Словарь свечей по таймфреймам
            
        Returns:
            Opportunity: Обнаруженные возможности
        """
        # Проверяем кэш
        cache_key = self._get_cache_key(symbol, candles_map)
        if cache_key in self._cache:
            cache_entry = self._cache[cache_key]
            if self._is_cache_valid(cache_entry):
                opportunity, _ = cache_entry
                return opportunity
            else:
                # Удаляем устаревший кэш
                del self._cache[cache_key]
        
        candles_15m = candles_map.get("15m", [])
        candles_30m = candles_map.get("30m", [])
        
        if not candles_15m:
            result = Opportunity(
                volatility_squeeze=False,
                accumulation=False,
                divergence=False,
                suspicious_silence=False,
                readiness_score=0.0
            )
            # Кэшируем даже пустой результат
            self._cache[cache_key] = (result, datetime.now(UTC))
            return result
        
        # 1. Проверка сжатия волатильности
        volatility_squeeze = self._check_volatility_squeeze(candles_15m)
        
        # 2. Проверка накопления
        accumulation = self._check_accumulation(candles_15m, candles_30m)
        
        # 3. Проверка расхождений
        divergence = self._check_divergence(candles_15m)
        
        # 4. Проверка подозрительной тишины
        suspicious_silence = self._check_suspicious_silence(candles_15m)
        
        # 5. Рассчитываем готовность
        readiness_score = self._calculate_readiness(
            volatility_squeeze, accumulation, divergence, suspicious_silence, candles_15m
        )
        
        result = Opportunity(
            volatility_squeeze=volatility_squeeze,
            accumulation=accumulation,
            divergence=divergence,
            suspicious_silence=suspicious_silence,
            readiness_score=readiness_score
        )
        
        # Сохраняем в кэш
        self._cache[cache_key] = (result, datetime.now(UTC))
        
        # Обновляем состояние в SystemState (если передан)
        if system_state is not None:
            system_state.update_opportunity(symbol, result)
        
        # Очищаем старый кэш (если больше 100 записей)
        if len(self._cache) > 100:
            self._cleanup_cache()
        
        return result
    
    def _cleanup_cache(self):
        """Очищает устаревшие записи из кэша"""
        now = datetime.now(UTC)
        keys_to_remove = [
            key for key, (_, timestamp) in self._cache.items()
            if now - timestamp >= self._cache_ttl
        ]
        for key in keys_to_remove:
            del self._cache[key]
    
    def _check_volatility_squeeze(self, candles: List) -> bool:
        """
        Проверяет сжатие волатильности (Bollinger Bands сужаются).
        """
        if len(candles) < 20:
            return False
        
        try:
            bb = bollinger_bands(candles, period=20)
            upper = bb.get("upper", [])
            lower = bb.get("lower", [])
            
            if len(upper) < 5 or len(lower) < 5:
                return False
            
            # Сравниваем ширину полос сейчас и 5 свечей назад
            current_width = upper[-1] - lower[-1]
            prev_width = upper[-5] - lower[-5]
            
            # Если ширина уменьшилась более чем на 20% - сжатие
            if prev_width > 0 and current_width < prev_width * 0.8:
                return True
        except:
            pass
        
        return False
    
    def _check_accumulation(self, candles_15m: List, candles_30m: List) -> bool:
        """
        Проверяет накопление (цена в диапазоне, объемы растут).
        """
        if len(candles_15m) < 20:
            return False
        
        # Проверяем, что цена в диапазоне
        atr_15m = atr(candles_15m)
        current_price = float(candles_15m[-1][4])
        
        # Берем диапазон последних 20 свечей
        highs = [float(c[2]) for c in candles_15m[-20:]]
        lows = [float(c[3]) for c in candles_15m[-20:]]
        
        price_range = max(highs) - min(lows)
        
        # Если диапазон меньше 2 ATR - возможное накопление
        if price_range < atr_15m * 2:
            # Проверяем объемы
            try:
                volume_data = volume_analysis(candles_15m, period=20)
                volume_trend = volume_data.get("trend", "NORMAL")
                
                # Если объемы растут при узком диапазоне - накопление
                if volume_trend == "INCREASING":
                    return True
            except Exception:
                pass
        
        return False
    
    def _check_divergence(self, candles: List) -> bool:
        """
        Проверяет расхождения между ценой и индикаторами.
        
        Ищет:
        - Бычье расхождение: цена делает новые минимумы, RSI растет
        - Медвежье расхождение: цена делает новые максимумы, RSI падает
        """
        if len(candles) < 20:
            return False
        
        try:
            # Берем последние 20 свечей для анализа
            recent_candles = candles[-20:]
            prices = [float(c[4]) for c in recent_candles]  # Close prices
            
            # Рассчитываем RSI
            rsi_values = []
            for i in range(14, len(recent_candles)):
                rsi_val = rsi(recent_candles[:i+1], period=14)
                rsi_values.append(rsi_val)
            
            if len(rsi_values) < 5:
                return False
            
            # Ищем расхождения
            # Бычье расхождение: цена падает, RSI растет
            price_lows = []
            rsi_lows = []
            
            for i in range(1, len(prices) - 1):
                if prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                    price_lows.append((i, prices[i]))
            
            for i in range(1, len(rsi_values) - 1):
                if rsi_values[i] < rsi_values[i-1] and rsi_values[i] < rsi_values[i+1]:
                    rsi_lows.append((i, rsi_values[i]))
            
            # Проверяем бычье расхождение
            if len(price_lows) >= 2 and len(rsi_lows) >= 2:
                # Последние два минимума цены
                last_price_low = price_lows[-1][1]
                prev_price_low = price_lows[-2][1]
                
                # Соответствующие минимумы RSI
                if len(rsi_lows) >= 2:
                    last_rsi_low = rsi_lows[-1][1]
                    prev_rsi_low = rsi_lows[-2][1]
                    
                    # Бычье расхождение: цена ниже, RSI выше
                    if last_price_low < prev_price_low and last_rsi_low > prev_rsi_low:
                        return True
            
            # Медвежье расхождение: цена растет, RSI падает
            price_highs = []
            rsi_highs = []
            
            for i in range(1, len(prices) - 1):
                if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                    price_highs.append((i, prices[i]))
            
            for i in range(1, len(rsi_values) - 1):
                if rsi_values[i] > rsi_values[i-1] and rsi_values[i] > rsi_values[i+1]:
                    rsi_highs.append((i, rsi_values[i]))
            
            if len(price_highs) >= 2 and len(rsi_highs) >= 2:
                last_price_high = price_highs[-1][1]
                prev_price_high = price_highs[-2][1]
                
                if len(rsi_highs) >= 2:
                    last_rsi_high = rsi_highs[-1][1]
                    prev_rsi_high = rsi_highs[-2][1]
                    
                    # Медвежье расхождение: цена выше, RSI ниже
                    if last_price_high > prev_price_high and last_rsi_high < prev_rsi_high:
                        return True
        
        except Exception:
            # Если ошибка при расчете - возвращаем False
            pass
        
        return False
    
    def _check_suspicious_silence(self, candles: List) -> bool:
        """
        Проверяет подозрительную тишину (низкая волатильность + низкие объемы).
        """
        if len(candles) < 20:
            return False
        
        # Проверяем волатильность
        metrics = calculate_volatility_metrics(candles)
        volatility_level = metrics.get("volatility_level", "NORMAL")
        
        if volatility_level != "LOW":
            return False
        
        # Проверяем объемы
        try:
            volume_data = volume_analysis(candles, period=20)
            volume_trend = volume_data.get("trend", "NORMAL")
            
            # Низкая волатильность + низкие/падающие объемы = подозрительная тишина
            if volume_trend in ["LOW", "DECREASING"]:
                return True
        except:
            pass
        
        return False
    
    def _calculate_readiness(self, volatility_squeeze: bool, accumulation: bool,
                            divergence: bool, suspicious_silence: bool,
                            candles: List) -> float:
        """
        Рассчитывает готовность рынка (0.0 - 1.0).
        """
        score = 0.0
        
        # Сжатие волатильности - хороший признак готовности
        if volatility_squeeze:
            score += 0.3
        
        # Накопление - хороший признак
        if accumulation:
            score += 0.3
        
        # Расхождение - может быть признаком разворота
        if divergence:
            score += 0.2
        
        # Подозрительная тишина - может предшествовать движению
        if suspicious_silence:
            score += 0.2
        
        # Дополнительные факторы
        if len(candles) >= 20:
            # Проверяем, что рынок не во флэте слишком долго
            atr_val = atr(candles)
            is_flat_market = is_flat(candles, atr_val)
            
            if not is_flat_market:
                score += 0.1
        
        return min(1.0, score)
    
    def reset(self):
        """
        Сбрасывает состояние brain.
        Полезно для тестирования и перезапуска анализа.
        """
        self.state = {}
        self._cache = {}


# Глобальный экземпляр
_opportunity_awareness = None

def get_opportunity_awareness() -> OpportunityAwareness:
    """Получить глобальный экземпляр Opportunity Awareness"""
    global _opportunity_awareness
    if _opportunity_awareness is None:
        _opportunity_awareness = OpportunityAwareness()
    return _opportunity_awareness

