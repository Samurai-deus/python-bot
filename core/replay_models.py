"""
Replay Models - модели данных для Replay Engine.

Replay Engine - оффлайн-инструмент аудита для повторного прогона
сохранённых SignalSnapshot через текущую логику принятия решений.

Чем Replay отличается от Backtest:
- Backtest: симулирует торговлю на исторических данных
- Replay: повторяет принятие решений на сохранённых snapshot'ах
- Replay выявляет расхождения в решениях (drift detection)
- Replay не торгует, только анализирует решения
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
from enum import Enum


class DecisionType(str, Enum):
    """Тип решения"""
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REDUCE = "REDUCE"
    SCALE_DOWN = "SCALE_DOWN"
    SKIP = "SKIP"
    OBSERVE = "OBSERVE"
    ENTER = "ENTER"
    NONE = "NONE"  # Решение не было принято


@dataclass
class OriginalDecision:
    """
    Оригинальное решение из snapshot.
    
    Содержит решение, которое было принято в момент создания snapshot.
    """
    decision_type: DecisionType
    decision_source: str  # Источник решения (например, "MetaDecisionBrain", "PortfolioBrain")
    reason: str
    block_level: Optional[str] = None  # HARD, SOFT, NONE
    position_allowed: Optional[bool] = None
    position_size_usd: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ReplayedDecision:
    """
    Решение, полученное при replay.
    
    Содержит решение, которое было бы принято при повторном прогоне
    через текущую логику принятия решений.
    """
    decision_type: DecisionType
    decision_source: str
    reason: str
    block_level: Optional[str] = None
    position_allowed: Optional[bool] = None
    position_size_usd: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DecisionDiff:
    """
    Разница между оригинальным и повторным решением.
    
    Содержит детальное сравнение решений для выявления расхождений.
    """
    decision_changed: bool
    decision_type_changed: bool
    reason_changed: bool
    block_level_changed: bool
    position_allowed_changed: bool
    position_size_changed: bool
    
    original_decision_type: DecisionType
    replayed_decision_type: DecisionType
    
    original_reason: str
    replayed_reason: str
    
    original_block_level: Optional[str]
    replayed_block_level: Optional[str]
    
    original_position_allowed: Optional[bool]
    replayed_position_allowed: Optional[bool]
    
    original_position_size: Optional[float]
    replayed_position_size: Optional[float]
    
    size_diff_pct: Optional[float] = None  # Процент изменения размера
    
    diff_summary: str = ""  # Краткое описание изменений
    
    def __post_init__(self):
        """Вычисляет summary после создания"""
        changes = []
        
        if self.decision_type_changed:
            changes.append(f"Decision: {self.original_decision_type.value} → {self.replayed_decision_type.value}")
        
        if self.block_level_changed:
            changes.append(f"Block level: {self.original_block_level} → {self.replayed_block_level}")
        
        if self.position_allowed_changed:
            changes.append(f"Position allowed: {self.original_position_allowed} → {self.replayed_position_allowed}")
        
        if self.position_size_changed and self.size_diff_pct is not None:
            changes.append(f"Position size: {self.size_diff_pct:+.1f}%")
        
        if self.reason_changed:
            changes.append("Reason changed")
        
        self.diff_summary = "; ".join(changes) if changes else "No changes"


@dataclass
class ReplayResult:
    """
    Результат replay для одного snapshot.
    
    Содержит оригинальное решение, повторное решение и их сравнение.
    """
    snapshot_id: int
    symbol: str
    timestamp: datetime
    
    original_decision: OriginalDecision
    replayed_decision: ReplayedDecision
    diff: DecisionDiff
    
    replay_timestamp: datetime = field(default_factory=lambda: datetime.now())
    
    def is_changed(self) -> bool:
        """Проверяет, изменилось ли решение"""
        return self.diff.decision_changed


@dataclass
class ReplayReport:
    """
    Агрегированный отчёт по результатам replay.
    
    Содержит статистику по всем проигранным snapshot'ам.
    """
    total_snapshots: int
    changed_decisions: int
    unchanged_decisions: int
    
    change_rate: float  # Процент изменённых решений (0.0 - 1.0)
    
    # Breakdown по причинам изменений
    meta_decision_changes: int = 0  # Изменения в MetaDecisionBrain
    portfolio_changes: int = 0  # Изменения в PortfolioBrain
    position_sizing_changes: int = 0  # Изменения в PositionSizer
    risk_changes: int = 0  # Изменения в уровне риска
    size_changes: int = 0  # Изменения в размере позиции
    
    # Breakdown по типам решений
    decision_type_changes: Dict[str, int] = field(default_factory=dict)
    
    # Детали изменений
    changed_results: List[ReplayResult] = field(default_factory=list)
    
    def __post_init__(self):
        """Вычисляет change_rate после создания"""
        if self.total_snapshots > 0:
            self.change_rate = self.changed_decisions / self.total_snapshots
        else:
            self.change_rate = 0.0

