"""
Decision Trace - система логирования решений торговой системы.

DecisionTrace НЕ влияет на торговую логику.
Он ТОЛЬКО записывает решения для:
- Анализа и отладки
- Replay сценариев
- Drift Detection (обнаружение дрейфа в решениях)
- Аудита и соответствия требованиям

Архитектура:
- Легковесная запись (быстро, без блокировок)
- SQLite для хранения
- Явная схема таблицы
- Готов к расширению для Replay / Drift Detector
"""
from dataclasses import dataclass, asdict
from datetime import datetime, UTC, timedelta
from typing import List, Optional, Dict, Any
from enum import Enum
import sqlite3
import os
import json
import logging

logger = logging.getLogger(__name__)

# Путь к базе данных (используем общую БД)
try:
    from database import DB_PATH
except ImportError:
    DB_PATH = os.environ.get("DB_PATH", "market_bot.db")


class BlockLevel(str, Enum):
    """Уровень блокировки"""
    HARD = "HARD"
    SOFT = "SOFT"
    NONE = "NONE"


@dataclass
class DecisionRecord:
    """
    Запись о решении торговой системы.
    
    Attributes:
        timestamp: Время принятия решения (UTC)
        symbol: Торговая пара (или "SYSTEM" для системных решений)
        decision_source: Источник решения (например, "MetaDecisionBrain", "PortfolioBrain", "Gatekeeper")
        allow_trading: Разрешена ли торговля
        block_level: Уровень блокировки (HARD, SOFT, NONE)
        reason: Объяснение решения
        context_snapshot: Снимок контекста на момент решения (dict)
    """
    timestamp: datetime
    symbol: str
    decision_source: str
    allow_trading: bool
    block_level: BlockLevel
    reason: str
    context_snapshot: Dict[str, Any]
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразует запись в словарь для сохранения в БД"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "decision_source": self.decision_source,
            "allow_trading": 1 if self.allow_trading else 0,
            "block_level": self.block_level.value,
            "reason": self.reason,
            "context_snapshot": json.dumps(self.context_snapshot) if self.context_snapshot else "{}"
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionRecord":
        """Создаёт DecisionRecord из словаря из БД"""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            symbol=data["symbol"],
            decision_source=data["decision_source"],
            allow_trading=bool(data["allow_trading"]),
            block_level=BlockLevel(data["block_level"]),
            reason=data["reason"],
            context_snapshot=json.loads(data["context_snapshot"]) if isinstance(data["context_snapshot"], str) else (data["context_snapshot"] if data["context_snapshot"] else {})
        )


class DecisionTrace:
    """
    Система логирования решений торговой системы.
    
    DecisionTrace записывает все решения системы для:
    - Анализа и отладки
    - Replay сценариев (воспроизведение решений)
    - Drift Detection (обнаружение дрейфа в решениях)
    - Аудита и соответствия требованиям
    
    Принципы:
    - НЕ влияет на торговую логику
    - ТОЛЬКО записывает решения
    - Легковесная запись (быстро, без блокировок)
    - Явная и простая схема таблицы
    """
    
    def __init__(self, db_path: str = DB_PATH):
        """
        Инициализация DecisionTrace.
        
        Args:
            db_path: Путь к базе данных SQLite
        """
        self.db_path = db_path
        self._init_database()
    
    def _get_connection(self) -> sqlite3.Connection:
        """
        Получает соединение с базой данных.
        
        Returns:
            sqlite3.Connection: Соединение с БД
        """
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # Для доступа к колонкам по имени
        return conn
    
    def _init_database(self):
        """
        Инициализирует схему базы данных.
        
        Создаёт таблицу decision_trace если её нет.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decision_trace (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                decision_source TEXT NOT NULL,
                allow_trading INTEGER NOT NULL,
                block_level TEXT NOT NULL,
                reason TEXT NOT NULL,
                context_snapshot TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Индексы для быстрого поиска
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decision_trace_timestamp 
            ON decision_trace(timestamp DESC)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decision_trace_symbol 
            ON decision_trace(symbol)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decision_trace_source 
            ON decision_trace(decision_source)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_decision_trace_allow_trading 
            ON decision_trace(allow_trading)
        """)
        
        conn.commit()
        conn.close()
    
    def log_decision(
        self,
        symbol: str,
        decision_source: str,
        allow_trading: bool,
        block_level: BlockLevel,
        reason: str,
        context_snapshot: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None
    ) -> int:
        """
        Записывает решение в базу данных.
        
        Args:
            symbol: Торговая пара (или "SYSTEM" для системных решений)
            decision_source: Источник решения (например, "MetaDecisionBrain", "PortfolioBrain", "Gatekeeper")
            allow_trading: Разрешена ли торговля
            block_level: Уровень блокировки (HARD, SOFT, NONE)
            reason: Объяснение решения
            context_snapshot: Снимок контекста на момент решения (опционально)
            timestamp: Время принятия решения (опционально, по умолчанию текущее время)
        
        Returns:
            int: ID записи в базе данных
        
        Примечание:
            Запись максимально лёгкая (быстро, без блокировок).
            В случае ошибки логируется, но не выбрасывается исключение.
        """
        if timestamp is None:
            timestamp = datetime.now(UTC)
        
        if context_snapshot is None:
            context_snapshot = {}
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO decision_trace 
                (timestamp, symbol, decision_source, allow_trading, block_level, reason, context_snapshot)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                symbol,
                decision_source,
                1 if allow_trading else 0,
                block_level.value,
                reason,
                json.dumps(context_snapshot) if context_snapshot else "{}"
            ))
            
            record_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return record_id
        except Exception as e:
            logger.error(f"Ошибка записи решения в DecisionTrace: {type(e).__name__}: {e}", exc_info=True)
            # Не выбрасываем исключение - логирование не должно влиять на торговую логику
            return -1
    
    def get_recent_decisions(
        self,
        limit: int = 100,
        symbol: Optional[str] = None,
        decision_source: Optional[str] = None,
        allow_trading: Optional[bool] = None
    ) -> List[DecisionRecord]:
        """
        Получает последние решения из базы данных.
        
        Args:
            limit: Максимальное количество записей
            symbol: Фильтр по символу (опционально)
            decision_source: Фильтр по источнику решения (опционально)
            allow_trading: Фильтр по разрешению торговли (опционально)
        
        Returns:
            List[DecisionRecord]: Список записей о решениях
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = "SELECT * FROM decision_trace WHERE 1=1"
            params = []
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            if decision_source:
                query += " AND decision_source = ?"
                params.append(decision_source)
            
            if allow_trading is not None:
                query += " AND allow_trading = ?"
                params.append(1 if allow_trading else 0)
            
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            records = []
            for row in rows:
                record_dict = {
                    "timestamp": row["timestamp"],
                    "symbol": row["symbol"],
                    "decision_source": row["decision_source"],
                    "allow_trading": row["allow_trading"],
                    "block_level": row["block_level"],
                    "reason": row["reason"],
                    "context_snapshot": row["context_snapshot"]
                }
                records.append(DecisionRecord.from_dict(record_dict))
            
            return records
        except Exception as e:
            logger.error(f"Ошибка получения решений из DecisionTrace: {type(e).__name__}: {e}", exc_info=True)
            return []
    
    def clear_old_records(self, days: int = 30) -> int:
        """
        Удаляет старые записи из базы данных.
        
        Args:
            days: Количество дней для хранения (записи старше будут удалены)
        
        Returns:
            int: Количество удалённых записей
        
        Примечание:
            Используется для управления размером базы данных.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            
            cursor.execute("""
                DELETE FROM decision_trace 
                WHERE timestamp < ?
            """, (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"Удалено {deleted_count} старых записей из DecisionTrace (старше {days} дней)")
            return deleted_count
        except Exception as e:
            logger.error(f"Ошибка удаления старых записей из DecisionTrace: {type(e).__name__}: {e}", exc_info=True)
            return 0
    
    def get_statistics(
        self,
        days: int = 7,
        symbol: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Получает статистику по решениям за период.
        
        Args:
            days: Количество дней для анализа
            symbol: Фильтр по символу (опционально)
        
        Returns:
            Dict[str, Any]: Статистика по решениям
        
        Примечание:
            Готово для использования в Drift Detector.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cutoff_date = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            
            query = """
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN allow_trading = 1 THEN 1 ELSE 0 END) as allowed,
                    SUM(CASE WHEN allow_trading = 0 THEN 1 ELSE 0 END) as blocked,
                    SUM(CASE WHEN block_level = 'HARD' THEN 1 ELSE 0 END) as hard_blocks,
                    SUM(CASE WHEN block_level = 'SOFT' THEN 1 ELSE 0 END) as soft_blocks,
                    decision_source,
                    COUNT(*) as count_by_source
                FROM decision_trace
                WHERE timestamp >= ?
            """
            
            params = [cutoff_date]
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            query += " GROUP BY decision_source"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            # Общая статистика
            cursor.execute("""
                SELECT 
                    COUNT(*) as total,
                    SUM(CASE WHEN allow_trading = 1 THEN 1 ELSE 0 END) as allowed,
                    SUM(CASE WHEN allow_trading = 0 THEN 1 ELSE 0 END) as blocked,
                    SUM(CASE WHEN block_level = 'HARD' THEN 1 ELSE 0 END) as hard_blocks,
                    SUM(CASE WHEN block_level = 'SOFT' THEN 1 ELSE 0 END) as soft_blocks
                FROM decision_trace
                WHERE timestamp >= ?
            """ + (" AND symbol = ?" if symbol else ""), params[:1] if not symbol else params)
            
            total_row = cursor.fetchone()
            conn.close()
            
            stats = {
                "period_days": days,
                "total_decisions": total_row["total"] if total_row else 0,
                "allowed": total_row["allowed"] if total_row else 0,
                "blocked": total_row["blocked"] if total_row else 0,
                "hard_blocks": total_row["hard_blocks"] if total_row else 0,
                "soft_blocks": total_row["soft_blocks"] if total_row else 0,
                "by_source": {}
            }
            
            for row in rows:
                stats["by_source"][row["decision_source"]] = {
                    "count": row["count_by_source"],
                    "allowed": sum(1 for r in self.get_recent_decisions(limit=1000, decision_source=row["decision_source"]) if r.allow_trading),
                    "blocked": sum(1 for r in self.get_recent_decisions(limit=1000, decision_source=row["decision_source"]) if not r.allow_trading)
                }
            
            return stats
        except Exception as e:
            logger.error(f"Ошибка получения статистики из DecisionTrace: {type(e).__name__}: {e}", exc_info=True)
            return {}


# ========== АРХИТЕКТУРА ДЛЯ REPLAY / DRIFT DETECTOR ==========

class DecisionReplay:
    """
    Класс для воспроизведения решений (Replay).
    
    Позволяет воспроизвести последовательность решений для анализа.
    """
    
    def __init__(self, decision_trace: DecisionTrace):
        """
        Инициализация DecisionReplay.
        
        Args:
            decision_trace: Экземпляр DecisionTrace
        """
        self.decision_trace = decision_trace
    
    def replay_decisions(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[DecisionRecord]:
        """
        Воспроизводит решения за период.
        
        Args:
            start_time: Начало периода
            end_time: Конец периода
            symbol: Фильтр по символу (опционально)
        
        Returns:
            List[DecisionRecord]: Список решений в хронологическом порядке
        """
        # Реализация для будущего использования
        all_decisions = self.decision_trace.get_recent_decisions(limit=10000, symbol=symbol)
        return [
            d for d in all_decisions
            if start_time <= d.timestamp <= end_time
        ]


class DriftDetector:
    """
    Класс для обнаружения дрейфа в решениях (Drift Detection).
    
    Анализирует изменения в паттернах решений со временем.
    """
    
    def __init__(self, decision_trace: DecisionTrace):
        """
        Инициализация DriftDetector.
        
        Args:
            decision_trace: Экземпляр DecisionTrace
        """
        self.decision_trace = decision_trace
    
    def detect_drift(
        self,
        baseline_days: int = 7,
        comparison_days: int = 7,
        threshold: float = 0.2
    ) -> Dict[str, Any]:
        """
        Обнаруживает дрейф в решениях.
        
        Args:
            baseline_days: Количество дней для базовой линии
            comparison_days: Количество дней для сравнения
            threshold: Порог для обнаружения дрейфа (0.0 - 1.0)
        
        Returns:
            Dict[str, Any]: Результаты обнаружения дрейфа
        
        Примечание:
            Сравнивает статистику базовой линии с текущей статистикой.
            Если разница превышает threshold, считается дрейфом.
        """
        # Реализация для будущего использования
        baseline_stats = self.decision_trace.get_statistics(days=baseline_days)
        comparison_stats = self.decision_trace.get_statistics(days=comparison_days)
        
        drift_detected = False
        drift_details = {}
        
        if baseline_stats.get("total_decisions", 0) > 0 and comparison_stats.get("total_decisions", 0) > 0:
            baseline_allow_rate = baseline_stats["allowed"] / baseline_stats["total_decisions"]
            comparison_allow_rate = comparison_stats["allowed"] / comparison_stats["total_decisions"]
            
            drift = abs(baseline_allow_rate - comparison_allow_rate)
            if drift > threshold:
                drift_detected = True
                drift_details = {
                    "baseline_allow_rate": baseline_allow_rate,
                    "comparison_allow_rate": comparison_allow_rate,
                    "drift": drift,
                    "threshold": threshold
                }
        
        return {
            "drift_detected": drift_detected,
            "details": drift_details,
            "baseline_stats": baseline_stats,
            "comparison_stats": comparison_stats
        }

