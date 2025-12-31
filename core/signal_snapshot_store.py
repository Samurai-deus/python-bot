"""
Signal Snapshot Store - хранилище для агрегированных снимков состояния системы.

SignalSnapshotStore фиксирует агрегированный снимок состояния рынка и системы
в момент генерации торгового сигнала или отказа от него.

Snapshot НЕ влияет на торговые решения.
Snapshot создаётся ДО MetaDecisionBrain и PositionSizer.

Архитектура:
- Легковесная запись (быстро, без блокировок)
- SQLite для хранения
- JSON для сериализации данных
- Готов к использованию в Replay Engine
"""
from dataclasses import dataclass, asdict
from datetime import datetime, UTC
from typing import Dict, List, Optional, Any
from enum import Enum
import sqlite3
import json
import os
import logging

logger = logging.getLogger(__name__)

# Путь к базе данных (используем общую БД)
try:
    from database import DB_PATH
except ImportError:
    DB_PATH = os.environ.get("DB_PATH", "market_bot.db")


class RiskLevel(str, Enum):
    """Уровень риска"""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


@dataclass
class SignalSnapshotRecord:
    """
    Агрегированный снимок состояния рынка и системы (запись для хранения).
    
    Фиксирует состояние в момент генерации торгового сигнала или отказа от него.
    Используется для:
    - Анализа и отладки
    - Replay Engine (воспроизведение решений)
    - Drift Detection (обнаружение дрейфа)
    - Аудита и соответствия требованиям
    
    Примечание:
        Это запись для хранения в БД, отличается от доменного объекта SignalSnapshot
        из core/signal_snapshot.py (который используется в runtime).
    
    Attributes:
        snapshot_id: Уникальный ID snapshot (генерируется при сохранении)
        timestamp: Время создания snapshot (UTC)
        symbol: Торговая пара
        states: Состояния по таймфреймам (Dict[str, MarketState как строка])
        confidence: Уверенность системы (0.0 - 1.0)
        entropy: Когнитивная неопределённость (0.0 - 1.0)
        score: Score сигнала
        risk_level: Уровень риска
        indicators: Словарь индикаторов
        portfolio_state: Агрегированное состояние портфеля
        decision_flags: Флаги решений (например, {"meta_decision": "ALLOW", "portfolio": "ALLOW"})
    """
    snapshot_id: Optional[int] = None
    timestamp: datetime = None
    symbol: str = ""
    states: Dict[str, str] = None  # MarketState как строки для JSON
    confidence: float = 0.0
    entropy: float = 0.0
    score: float = 0.0
    risk_level: str = ""
    indicators: Dict[str, Any] = None
    portfolio_state: Dict[str, Any] = None
    decision_flags: Dict[str, Any] = None
    
    def __post_init__(self):
        """Инициализация значений по умолчанию"""
        if self.timestamp is None:
            self.timestamp = datetime.now(UTC)
        if self.states is None:
            self.states = {}
        if self.indicators is None:
            self.indicators = {}
        if self.portfolio_state is None:
            self.portfolio_state = {}
        if self.decision_flags is None:
            self.decision_flags = {}
    
    def to_dict(self) -> Dict[str, Any]:
        """Преобразует snapshot в словарь для сохранения в БД"""
        return {
            "snapshot_id": self.snapshot_id,
            "timestamp": self.timestamp.isoformat(),
            "symbol": self.symbol,
            "states": self.states,
            "confidence": self.confidence,
            "entropy": self.entropy,
            "score": self.score,
            "risk_level": self.risk_level,
            "indicators": self.indicators,
            "portfolio_state": self.portfolio_state,
            "decision_flags": self.decision_flags
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignalSnapshotRecord":
        """Создаёт SignalSnapshotRecord из словаря из БД"""
        return cls(
            snapshot_id=data.get("snapshot_id"),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            symbol=data["symbol"],
            states=data.get("states", {}),
            confidence=data.get("confidence", 0.0),
            entropy=data.get("entropy", 0.0),
            score=data.get("score", 0.0),
            risk_level=data.get("risk_level", ""),
            indicators=data.get("indicators", {}),
            portfolio_state=data.get("portfolio_state", {}),
            decision_flags=data.get("decision_flags", {})
        )


class SignalSnapshotStore:
    """
    Хранилище для агрегированных снимков состояния системы.
    
    SignalSnapshotStore фиксирует состояние рынка и системы в момент
    генерации торгового сигнала или отказа от него.
    
    Принципы:
    - НЕ влияет на торговые решения
    - Создаётся ДО MetaDecisionBrain и PositionSizer
    - Легковесная запись (быстро, без блокировок)
    - JSON для сериализации данных
    """
    
    def __init__(self, db_path: str = DB_PATH):
        """
        Инициализация SignalSnapshotStore.
        
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
        
        Создаёт таблицу signal_snapshots если её нет.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signal_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                states TEXT NOT NULL,
                confidence REAL NOT NULL,
                entropy REAL NOT NULL,
                score REAL NOT NULL,
                risk_level TEXT NOT NULL,
                indicators TEXT NOT NULL,
                portfolio_state TEXT NOT NULL,
                decision_flags TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Индексы для быстрого поиска
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_timestamp 
            ON signal_snapshots(timestamp DESC)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_symbol 
            ON signal_snapshots(symbol)
        """)
        
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_symbol_timestamp 
            ON signal_snapshots(symbol, timestamp DESC)
        """)
        
        conn.commit()
        conn.close()
    
    def save_snapshot(
        self,
        timestamp: datetime,
        symbol: str,
        states: Dict[str, str],
        confidence: float,
        entropy: float,
        score: float,
        risk_level: str,
        indicators: Optional[Dict[str, Any]] = None,
        portfolio_state: Optional[Dict[str, Any]] = None,
        decision_flags: Optional[Dict[str, Any]] = None
    ) -> int:
        """
        Сохраняет snapshot в базу данных.
        
        Args:
            timestamp: Время создания snapshot
            symbol: Торговая пара
            states: Состояния по таймфреймам (Dict[str, MarketState как строка])
            confidence: Уверенность системы (0.0 - 1.0)
            entropy: Когнитивная неопределённость (0.0 - 1.0)
            score: Score сигнала
            risk_level: Уровень риска ("LOW", "MEDIUM", "HIGH")
            indicators: Словарь индикаторов (опционально)
            portfolio_state: Агрегированное состояние портфеля (опционально)
            decision_flags: Флаги решений (опционально)
        
        Returns:
            int: ID сохранённого snapshot
        
        Примечание:
            Запись максимально лёгкая (быстро, без блокировок).
            В случае ошибки логируется, но не выбрасывается исключение.
        """
        if indicators is None:
            indicators = {}
        if portfolio_state is None:
            portfolio_state = {}
        if decision_flags is None:
            decision_flags = {}
        
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO signal_snapshots 
                (timestamp, symbol, states, confidence, entropy, score, risk_level, 
                 indicators, portfolio_state, decision_flags)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                timestamp.isoformat(),
                symbol,
                json.dumps(states),
                confidence,
                entropy,
                score,
                risk_level,
                json.dumps(indicators),
                json.dumps(portfolio_state),
                json.dumps(decision_flags)
            ))
            
            snapshot_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            return snapshot_id
        except Exception as e:
            logger.error(f"Ошибка сохранения snapshot в SignalSnapshotStore: {type(e).__name__}: {e}", exc_info=True)
            # Не выбрасываем исключение - сохранение не должно влиять на торговую логику
            return -1
    
    def get_snapshot_by_id(self, snapshot_id: int) -> Optional[SignalSnapshotRecord]:
        """
        Получает snapshot по ID.
        
        Args:
            snapshot_id: ID snapshot
        
        Returns:
            SignalSnapshotRecord или None если не найден
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM signal_snapshots WHERE id = ?
            """, (snapshot_id,))
            
            row = cursor.fetchone()
            conn.close()
            
            if row is None:
                return None
            
            return self._row_to_snapshot(row)
        except Exception as e:
            logger.error(f"Ошибка получения snapshot по ID {snapshot_id}: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    def get_recent_snapshots(
        self,
        symbol: Optional[str] = None,
        limit: int = 100
    ) -> List[SignalSnapshotRecord]:
        """
        Получает последние snapshot'ы.
        
        Args:
            symbol: Фильтр по символу (опционально)
            limit: Максимальное количество snapshot'ов
        
        Returns:
            List[SignalSnapshotRecord]: Список snapshot'ов
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            if symbol:
                cursor.execute("""
                    SELECT * FROM signal_snapshots 
                    WHERE symbol = ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (symbol, limit))
            else:
                cursor.execute("""
                    SELECT * FROM signal_snapshots 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                """, (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            snapshots = []
            for row in rows:
                snapshot = self._row_to_snapshot(row)
                if snapshot:
                    snapshots.append(snapshot)
            
            return snapshots
        except Exception as e:
            logger.error(f"Ошибка получения snapshot'ов: {type(e).__name__}: {e}", exc_info=True)
            return []
    
    def _row_to_snapshot(self, row: sqlite3.Row) -> Optional[SignalSnapshotRecord]:
        """
        Преобразует строку из БД в SignalSnapshotRecord.
        
        Args:
            row: Строка из БД
        
        Returns:
            SignalSnapshotRecord или None при ошибке
        """
        try:
            return SignalSnapshotRecord(
                snapshot_id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                symbol=row["symbol"],
                states=json.loads(row["states"]),
                confidence=row["confidence"],
                entropy=row["entropy"],
                score=row["score"],
                risk_level=row["risk_level"],
                indicators=json.loads(row["indicators"]),
                portfolio_state=json.loads(row["portfolio_state"]),
                decision_flags=json.loads(row["decision_flags"])
            )
        except Exception as e:
            logger.error(f"Ошибка преобразования строки в SignalSnapshotRecord: {type(e).__name__}: {e}", exc_info=True)
            return None
    
    def get_snapshots_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[SignalSnapshotRecord]:
        """
        Получает snapshot'ы за период времени.
        
        Используется для Replay Engine.
        
        Args:
            start_time: Начало периода
            end_time: Конец периода
            symbol: Фильтр по символу (опционально)
        
        Returns:
            List[SignalSnapshotRecord]: Список snapshot'ов в хронологическом порядке
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            query = """
                SELECT * FROM signal_snapshots 
                WHERE timestamp >= ? AND timestamp <= ?
            """
            params = [start_time.isoformat(), end_time.isoformat()]
            
            if symbol:
                query += " AND symbol = ?"
                params.append(symbol)
            
            query += " ORDER BY timestamp ASC"
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            snapshots = []
            for row in rows:
                snapshot = self._row_to_snapshot(row)
                if snapshot:
                    snapshots.append(snapshot)
            
            return snapshots
        except Exception as e:
            logger.error(f"Ошибка получения snapshot'ов за период: {type(e).__name__}: {e}", exc_info=True)
            return []
    
    def clear_old_snapshots(self, days: int = 30) -> int:
        """
        Удаляет старые snapshot'ы из базы данных.
        
        Args:
            days: Количество дней для хранения (snapshot'ы старше будут удалены)
        
        Returns:
            int: Количество удалённых snapshot'ов
        
        Примечание:
            Используется для управления размером базы данных.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            from datetime import timedelta
            cutoff_date = (datetime.now(UTC) - timedelta(days=days)).isoformat()
            
            cursor.execute("""
                DELETE FROM signal_snapshots 
                WHERE timestamp < ?
            """, (cutoff_date,))
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"Удалено {deleted_count} старых snapshot'ов из SignalSnapshotStore (старше {days} дней)")
            return deleted_count
        except Exception as e:
            logger.error(f"Ошибка удаления старых snapshot'ов: {type(e).__name__}: {e}", exc_info=True)
            return 0


# ========== АРХИТЕКТУРА ДЛЯ REPLAY ENGINE ==========

class ReplayEngine:
    """
    Движок для воспроизведения snapshot'ов (Replay).
    
    Позволяет воспроизвести последовательность snapshot'ов для анализа.
    """
    
    def __init__(self, snapshot_store: SignalSnapshotStore):
        """
        Инициализация ReplayEngine.
        
        Args:
            snapshot_store: Экземпляр SignalSnapshotStore
        """
        self.snapshot_store = snapshot_store
    
    def replay_snapshots(
        self,
        start_time: datetime,
        end_time: datetime,
        symbol: Optional[str] = None
    ) -> List[SignalSnapshotRecord]:
        """
        Воспроизводит snapshot'ы за период.
        
        Args:
            start_time: Начало периода
            end_time: Конец периода
            symbol: Фильтр по символу (опционально)
        
        Returns:
            List[SignalSnapshotRecord]: Список snapshot'ов в хронологическом порядке
        """
        return self.snapshot_store.get_snapshots_by_time_range(
            start_time=start_time,
            end_time=end_time,
            symbol=symbol
        )
    
    def replay_signal_generation(
        self,
        snapshot: SignalSnapshotRecord
    ) -> Dict[str, Any]:
        """
        Воспроизводит процесс генерации сигнала из snapshot.
        
        Args:
            snapshot: SignalSnapshotRecord для воспроизведения
        
        Returns:
            Dict[str, Any]: Результат воспроизведения
        """
        # Реализация для будущего использования
        return {
            "snapshot_id": snapshot.snapshot_id,
            "symbol": snapshot.symbol,
            "timestamp": snapshot.timestamp.isoformat(),
            "states": snapshot.states,
            "confidence": snapshot.confidence,
            "entropy": snapshot.entropy,
            "score": snapshot.score,
            "risk_level": snapshot.risk_level,
            "indicators": snapshot.indicators,
            "portfolio_state": snapshot.portfolio_state,
            "decision_flags": snapshot.decision_flags
        }


# ========== ПУБЛИЧНЫЙ API ДЛЯ ЗАГРУЗКИ SNAPSHOT'ОВ ==========

def _convert_record_to_snapshot(record: SignalSnapshotRecord) -> Optional['SignalSnapshot']:
    """
    Конвертирует SignalSnapshotRecord в доменный объект SignalSnapshot.
    
    Args:
        record: SignalSnapshotRecord из хранилища
    
    Returns:
        SignalSnapshot или None при ошибке конвертации
    """
    try:
        from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel
        from core.market_state import MarketState, normalize_states_dict
        from core.decision_core import MarketRegime
        
        # Нормализуем states (конвертируем строки в MarketState enum)
        states_normalized = {}
        for key, state_str in record.states.items():
            if state_str is None or state_str == "":
                states_normalized[key] = None
            else:
                try:
                    # Пытаемся найти соответствующий MarketState enum
                    states_normalized[key] = MarketState[state_str]
                except (KeyError, AttributeError):
                    states_normalized[key] = None
        
        # Нормализуем через normalize_states_dict для консистентности
        states_normalized = normalize_states_dict(states_normalized)
        
        # Определяем timeframe_anchor (берём первый доступный таймфрейм)
        if "15m" in record.states:
            timeframe_anchor = "15m"
        elif "30m" in record.states:
            timeframe_anchor = "30m"
        elif "1h" in record.states:
            timeframe_anchor = "1h"
        else:
            # Используем первый доступный таймфрейм или дефолт
            timeframe_anchor = list(record.states.keys())[0] if record.states else "15m"
        
        # Конвертируем risk_level
        try:
            risk_level = RiskLevel[record.risk_level] if record.risk_level else RiskLevel.MEDIUM
        except (KeyError, AttributeError):
            risk_level = RiskLevel.MEDIUM
        
        # Извлекаем market_regime из indicators или portfolio_state
        market_regime = None
        if record.indicators and "market_regime" in record.indicators:
            regime_data = record.indicators["market_regime"]
            if isinstance(regime_data, dict):
                market_regime = MarketRegime(
                    trend_type=regime_data.get("trend_type", "UNKNOWN"),
                    volatility_level=regime_data.get("volatility_level", "UNKNOWN"),
                    risk_sentiment=regime_data.get("risk_sentiment", "UNKNOWN"),
                    confidence=regime_data.get("confidence", 0.0)
                )
        
        # Извлекаем volatility_level
        volatility_level = None
        if record.indicators and "volatility_level" in record.indicators:
            vol_str = record.indicators["volatility_level"]
            try:
                volatility_level = VolatilityLevel[vol_str] if vol_str else None
            except (KeyError, AttributeError):
                volatility_level = None
        
        # Извлекаем decision из decision_flags
        decision = SignalDecision.SKIP
        decision_reason = ""
        if record.decision_flags:
            decision_str = record.decision_flags.get("decision", "SKIP")
            try:
                decision = SignalDecision[decision_str] if decision_str else SignalDecision.SKIP
            except (KeyError, AttributeError):
                decision = SignalDecision.SKIP
            decision_reason = record.decision_flags.get("reason", "")
        
        # Извлекаем entry zone из indicators
        entry = record.indicators.get("entry") if record.indicators else None
        tp = record.indicators.get("tp") if record.indicators else None
        sl = record.indicators.get("sl") if record.indicators else None
        
        # Извлекаем directions из indicators
        directions = record.indicators.get("directions", {}) if record.indicators else {}
        
        # Извлекаем score_details из indicators
        score_details = record.indicators.get("score_details", {}) if record.indicators else {}
        
        # Извлекаем reasons из decision_flags
        reasons = record.decision_flags.get("reasons", []) if record.decision_flags else []
        
        # Создаём SignalSnapshot
        snapshot = SignalSnapshot(
            timestamp=record.timestamp,
            symbol=record.symbol,
            timeframe_anchor=timeframe_anchor,
            states=states_normalized,
            market_regime=market_regime,
            volatility_level=volatility_level,
            correlation_level=record.indicators.get("correlation_level") if record.indicators else None,
            score=int(record.score),
            confidence=record.confidence,
            entropy=record.entropy,
            risk_level=risk_level,
            recommended_leverage=record.indicators.get("recommended_leverage") if record.indicators else None,
            tp=tp,
            sl=sl,
            entry=entry,
            decision=decision,
            decision_reason=decision_reason,
            directions=directions,
            score_details=score_details,
            reasons=reasons
        )
        
        return snapshot
    except Exception as e:
        logger.warning(f"Ошибка конвертации SignalSnapshotRecord в SignalSnapshot: {type(e).__name__}: {e}")
        return None


def load_last_snapshots(limit: int = 100) -> List['SignalSnapshot']:
    """
    Load last N SignalSnapshot objects from persistent storage.
    
    Intended for replay, drift detection and debugging.
    
    Args:
        limit: Maximum number of snapshots to load
    
    Returns:
        List[SignalSnapshot]: List of snapshots sorted by timestamp (oldest first)
    
    Note:
        This function does NOT have side effects.
        This function does NOT initialize runtime systems.
        Safe to call from any context (replay, tests, debugging).
    """
    try:
        from core.signal_snapshot import SignalSnapshot
        
        # Создаём store (лёгкая операция, не инициализирует runtime)
        store = SignalSnapshotStore()
        
        # Получаем записи (отсортированы по timestamp DESC)
        records = store.get_recent_snapshots(limit=limit)
        
        # Конвертируем в доменные объекты
        snapshots = []
        for record in records:
            snapshot = _convert_record_to_snapshot(record)
            if snapshot:
                snapshots.append(snapshot)
        
        # Возвращаем отсортированные от старых к новым (для хронологического порядка)
        snapshots.sort(key=lambda s: s.timestamp)
        
        return snapshots
    except Exception as e:
        logger.error(f"Ошибка загрузки snapshot'ов: {type(e).__name__}: {e}", exc_info=True)
        return []


# Экспортируем публичный API
__all__ = [
    'SignalSnapshotStore',
    'SignalSnapshotRecord',
    'load_last_snapshots',
    'ReplayEngine'
]

