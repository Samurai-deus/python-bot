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
from trade_manager import get_open_trades


class CognitiveFilter:
    """
    Фильтр человеческих ошибок.
    
    НЕ даёт сигналов, только анализирует поведение.
    """
    
    def __init__(self):
        self.max_trades_per_hour = 8  # Максимум новых сделок в час (бот сканирует 28 символов)
        self.max_trades_per_day = 30  # Максимум сделок в день
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
        - Частоту открытых сделок (не сигналов — бот сканирует 28 символов)
        - Количество открытых позиций
        - Серии убыточных сделок
        """
        score = 0.0

        # Считаем реально открытые сделки за час (не сигналы из журнала)
        open_trades = get_open_trades()

        # Проверяем количество открытых позиций
        open_count = len(open_trades)
        if open_count > 5:
            score += 0.3
        if open_count > 10:
            score += 0.4

        # Проверяем частоту новых сделок за последний час по таблице trades
        now = datetime.now(UTC)
        hour_ago = now - timedelta(hours=1)
        hour_ago_iso = hour_ago.isoformat()
        try:
            from database import get_db_connection, _q
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    _q("SELECT count(*) as cnt FROM trades WHERE created_at > ?"),
                    (hour_ago_iso,)
                )
                row = cursor.fetchone()
                new_trades_hour = row["cnt"] if row else 0
            finally:
                conn.close()
        except Exception:
            new_trades_hour = 0

        if new_trades_hour > self.max_trades_per_hour:
            score += 0.4
            score += min(0.3, (new_trades_hour - self.max_trades_per_hour) * 0.1)

        return min(1.0, score)
    
    def _get_recent_trades_from_db(self, hours: int = 24) -> list:
        """Получает реальные сделки из таблицы trades за указанный период."""
        since_iso = (datetime.now(UTC) - timedelta(hours=hours)).isoformat()
        try:
            from database import get_db_connection, _q
            conn = get_db_connection()
            try:
                cursor = conn.cursor()
                cursor.execute(
                    _q("SELECT id, created_at, symbol, side, pnl FROM trades WHERE created_at > ? ORDER BY created_at"),
                    (since_iso,)
                )
                return [dict(row) for row in cursor.fetchall()]
            finally:
                conn.close()
        except Exception:
            return []

    def _count_emotional_entries(self) -> int:
        """
        Подсчитывает эмоциональные входы по реальным сделкам.

        Признаки:
        - Сделка сразу после убыточной
        - Две сделки за < 2 минуты
        """
        trades = self._get_recent_trades_from_db(hours=6)
        if len(trades) < 2:
            return 0

        emotional_count = 0
        for i in range(1, len(trades)):
            prev_t = trades[i - 1].get("created_at", "")
            curr_t = trades[i].get("created_at", "")
            try:
                prev_dt = datetime.fromisoformat(prev_t.replace("Z", "+00:00"))
                curr_dt = datetime.fromisoformat(curr_t.replace("Z", "+00:00"))
                diff = (curr_dt - prev_dt).total_seconds()
            except (ValueError, TypeError):
                continue

            # Две сделки за < 2 минуты — подозрительно
            if diff < 120:
                emotional_count += 1

            # Вход сразу после убытка
            prev_pnl = trades[i - 1].get("pnl")
            if prev_pnl is not None and prev_pnl < 0 and diff < 300:
                emotional_count += 1

        return emotional_count

    def _count_fomo_patterns(self) -> int:
        """
        Подсчитывает FOMO паттерны по реальным сделкам.
        Много сделок за 6 часов — признак FOMO.
        """
        trades = self._get_recent_trades_from_db(hours=6)
        if len(trades) > 15:
            return len(trades) - 15
        return 0

    def _count_recent_trades(self) -> int:
        """Подсчитывает количество реальных сделок за последние 24 часа."""
        return len(self._get_recent_trades_from_db(hours=24))
    
    def _should_pause(self, overtrading_score: float, emotional_entries: int,
                     fomo_patterns: int, recent_trades_count: int) -> bool:
        """
        Определяет, нужна ли пауза.
        """
        # Пауза при высокой пере-торговле
        if overtrading_score > self.overtrading_threshold:
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

