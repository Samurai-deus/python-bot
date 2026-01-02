"""
Cognitive Filter Bot - фильтр человеческих ошибок

Отслеживает:
- пере-торговлю
- эмоциональные входы
- FOMO-паттерны
"""
from typing import Dict, List, Optional
from datetime import datetime, UTC, timedelta
from core.decision_core import CognitiveState
from journal import get_recent_signals
from trade_manager import get_open_trades


class CognitiveFilter:
    """
    Фильтр человеческих ошибок.
    
    НЕ даёт сигналов, только анализирует поведение.
    """
    
    def __init__(self):
        self.max_trades_per_hour = 3  # Максимум сделок в час
        self.max_trades_per_day = 10  # Максимум сделок в день
        self.overtrading_threshold = 0.7  # Порог пере-торговли
        # Состояние теперь хранится в SystemState, не здесь
    
    def reset(self):
        """
        Сбрасывает состояние brain (теперь не нужно - состояние в SystemState).
        Оставлено для обратной совместимости.
        """
        pass
    
    def analyze(self, system_state=None) -> CognitiveState:
        """
        Анализирует когнитивное состояние трейдера.
        
        Returns:
            CognitiveState: Текущее состояние
        """
        # 1. Анализ пере-торговли
        overtrading_score = self._analyze_overtrading()
        
        # 2. Подсчет эмоциональных входов
        emotional_entries = self._count_emotional_entries()
        
        # 3. Подсчет FOMO паттернов
        fomo_patterns = self._count_fomo_patterns()
        
        # 4. Количество недавних сделок
        recent_trades_count = self._count_recent_trades()
        
        # 5. Решение о паузе
        should_pause = self._should_pause(
            overtrading_score, emotional_entries, fomo_patterns, recent_trades_count
        )
        
        cognitive_state = CognitiveState(
            overtrading_score=overtrading_score,
            emotional_entries=emotional_entries,
            fomo_patterns=fomo_patterns,
            recent_trades_count=recent_trades_count,
            should_pause=should_pause
        )
        
        # Обновляем состояние в SystemState (если передан)
        if system_state is not None:
            system_state.update_cognitive_state(cognitive_state)
        
        return cognitive_state
    
    def _analyze_overtrading(self) -> float:
        """
        Анализирует пере-торговлю (0.0 - 1.0).
        
        Проверяет:
        - Частоту входов
        - Количество открытых позиций
        - Серии убыточных сделок
        """
        score = 0.0
        
        # Проверяем частоту входов за последний час
        now = datetime.now(UTC)
        hour_ago = now - timedelta(hours=1)
        
        recent_signals = get_recent_signals(since=hour_ago)
        signals_count = len(recent_signals)
        
        if signals_count > self.max_trades_per_hour:
            score += 0.5
            score += min(0.3, (signals_count - self.max_trades_per_hour) * 0.1)
        
        # Проверяем количество открытых позиций
        open_trades = get_open_trades()
        if len(open_trades) > 5:
            score += 0.2
        if len(open_trades) > 10:
            score += 0.3
        
        # Проверяем серии убыточных сделок (упрощенно)
        # В будущем можно добавить анализ закрытых сделок
        
        return min(1.0, score)
    
    def _count_emotional_entries(self) -> int:
        """
        Подсчитывает эмоциональные входы.
        
        Признаки эмоционального входа:
        - Вход сразу после убыточной сделки
        - Вход в неподходящее время
        - Вход без четкого сигнала
        - Слишком частые входы
        """
        recent_signals = get_recent_signals(since=datetime.now(UTC) - timedelta(hours=24))
        
        if len(recent_signals) < 2:
            return 0
        
        emotional_count = 0
        
        # Проверяем частоту входов
        signals_by_time = sorted(recent_signals, key=lambda x: x.get("timestamp", datetime.min.replace(tzinfo=UTC)))
        
        for i in range(1, len(signals_by_time)):
            prev_signal = signals_by_time[i-1]
            curr_signal = signals_by_time[i]
            
            prev_time = prev_signal.get("timestamp", datetime.min.replace(tzinfo=UTC))
            curr_time = curr_signal.get("timestamp", datetime.min.replace(tzinfo=UTC))
            
            time_diff = (curr_time - prev_time).total_seconds()
            
            # Если сигналы пришли менее чем за 5 минут - возможен эмоциональный вход
            if time_diff < 300:  # 5 минут
                emotional_count += 1
            
            # Если сигналы пришли менее чем за 1 минуту - точно эмоциональный
            if time_diff < 60:  # 1 минута
                emotional_count += 1
        
        # Проверяем входы в неподходящее время (ночью UTC)
        for signal in recent_signals:
            signal_time = signal.get("timestamp", datetime.now(UTC))
            hour = signal_time.hour
            
            # Входы между 0:00 и 6:00 UTC могут быть эмоциональными
            if 0 <= hour < 6:
                emotional_count += 1
        
        return emotional_count
    
    def _count_fomo_patterns(self) -> int:
        """
        Подсчитывает FOMO паттерны.
        
        Признаки FOMO:
        - Вход после резкого движения
        - Вход на пике/дне
        - Вход без подтверждения
        """
        # Упрощенная логика
        # В будущем можно добавить анализ резких движений перед входом
        
        fomo_count = 0
        
        # Проверяем недавние сигналы
        recent_signals = get_recent_signals(since=datetime.now(UTC) - timedelta(hours=6))
        
        # Если много сигналов за короткое время - возможен FOMO
        if len(recent_signals) > 5:
            fomo_count += len(recent_signals) - 5
        
        return fomo_count
    
    def _count_recent_trades(self) -> int:
        """Подсчитывает количество сделок за последние 24 часа"""
        recent_signals = get_recent_signals(since=datetime.now(UTC) - timedelta(hours=24))
        return len(recent_signals)
    
    def _should_pause(self, overtrading_score: float, emotional_entries: int,
                     fomo_patterns: int, recent_trades_count: int) -> bool:
        """
        Определяет, нужна ли пауза.
        """
        # Пауза при высокой пере-торговле
        if overtrading_score > self.overtrading_threshold:
            return True
        
        # Пауза при слишком частых сделках
        if recent_trades_count > self.max_trades_per_day:
            return True
        
        # Пауза при множественных FOMO паттернах
        if fomo_patterns > 3:
            return True
        
        return False


# Глобальный экземпляр
_cognitive_filter = None

def get_cognitive_filter() -> CognitiveFilter:
    """Получить глобальный экземпляр Cognitive Filter"""
    global _cognitive_filter
    if _cognitive_filter is None:
        _cognitive_filter = CognitiveFilter()
    return _cognitive_filter

