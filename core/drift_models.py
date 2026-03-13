"""
Drift Models - модели данных для Drift Detector.

Drift Detector выявляет деградацию поведения системы
через анализ entropy и confidence во времени.

Чем Drift отличается от Drawdown:
- Drawdown: снижение капитала/прибыли (финансовый показатель)
- Drift: изменение поведения системы (когнитивный показатель)
- Drift может происходить БЕЗ drawdown (система работает, но по-другому)
- Drawdown может происходить БЕЗ drift (временные рыночные условия)

Почему entropy и confidence - ведущие индикаторы деградации:
- Confidence: показывает, насколько система уверена в своих решениях
  - Низкая confidence → система не уверена → возможна деградация
  - Высокая confidence при плохих результатах → переобучение/overfitting
- Entropy: показывает структурированность рынка
  - Высокая entropy → рынок хаотичен → система может работать хуже
  - Низкая entropy → рынок структурирован → система должна работать лучше
- Decoupling (рассогласование): когда confidence и entropy не согласованы
  - Высокая confidence + высокая entropy → система переоценивает себя
  - Низкая confidence + низкая entropy → система недооценивает возможности
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Dict, Any
from enum import Enum


class DriftSeverity(str, Enum):
    """Уровень серьёзности drift"""
    LOW = "LOW"  # Незначительный drift, мониторинг
    MEDIUM = "MEDIUM"  # Заметный drift, требует внимания
    HIGH = "HIGH"  # Критический drift, требует вмешательства


class DriftType(str, Enum):
    """Тип drift"""
    CONFIDENCE = "CONFIDENCE"  # Drift в confidence
    ENTROPY = "ENTROPY"  # Drift в entropy
    DECOUPLING = "DECOUPLING"  # Рассогласование confidence и entropy
    OVERALL = "OVERALL"  # Общий drift


@dataclass
class DriftMetrics:
    """
    Метрики для анализа drift.
    
    Содержит статистические метрики для recent_window и baseline_window.
    """
    # Confidence метрики
    confidence_mean_recent: float
    confidence_mean_baseline: float
    confidence_variance_recent: float
    confidence_variance_baseline: float
    confidence_p90_recent: float
    confidence_p95_recent: float
    confidence_p90_baseline: float
    confidence_p95_baseline: float
    
    # Entropy метрики
    entropy_mean_recent: float
    entropy_mean_baseline: float
    entropy_variance_recent: float
    entropy_variance_baseline: float
    entropy_p90_recent: float
    entropy_p95_recent: float
    entropy_p90_baseline: float
    entropy_p95_baseline: float
    
    # Correlation метрики
    correlation_recent: float  # Корреляция confidence и entropy в recent_window
    correlation_baseline: float  # Корреляция confidence и entropy в baseline_window
    
    # Размеры окон
    recent_window_size: int
    baseline_window_size: int


@dataclass
class ConfidenceDrift:
    """
    Drift в confidence.
    
    Обнаруживается, когда средняя confidence в recent_window
    значительно отличается от baseline_window.
    """
    detected: bool
    severity: DriftSeverity
    reason: str
    
    # Метрики
    mean_diff: float  # Разница средних значений
    mean_diff_pct: float  # Процент изменения среднего
    variance_diff: float  # Разница дисперсий
    percentile_shift: float  # Сдвиг перцентилей
    
    # Пороги
    mean_threshold: float = 0.15  # Порог для среднего (15%)
    variance_threshold: float = 0.3  # Порог для дисперсии (30%)
    percentile_threshold: float = 0.1  # Порог для перцентилей (10%)


@dataclass
class EntropyDrift:
    """
    Drift в entropy.
    
    Обнаруживается, когда средняя entropy в recent_window
    значительно отличается от baseline_window.
    """
    detected: bool
    severity: DriftSeverity
    reason: str
    
    # Метрики
    mean_diff: float  # Разница средних значений
    mean_diff_pct: float  # Процент изменения среднего
    variance_diff: float  # Разница дисперсий
    percentile_shift: float  # Сдвиг перцентилей
    
    # Пороги
    mean_threshold: float = 0.15  # Порог для среднего (15%)
    variance_threshold: float = 0.3  # Порог для дисперсии (30%)
    percentile_threshold: float = 0.1  # Порог для перцентилей (10%)


@dataclass
class DecouplingDrift:
    """
    Рассогласование confidence и entropy.
    
    Обнаруживается, когда корреляция между confidence и entropy
    в recent_window значительно отличается от baseline_window.
    """
    detected: bool
    severity: DriftSeverity
    reason: str
    
    # Метрики
    correlation_diff: float  # Разница корреляций
    correlation_recent: float
    correlation_baseline: float
    
    # Пороги
    correlation_threshold: float = 0.2  # Порог для изменения корреляции (20%)


@dataclass
class DriftState:
    """
    Состояние drift для системы.
    
    Содержит флаги по каждому типу drift, severity и текстовое объяснение.
    Передаётся в MetaDecisionBrain для принятия решений.
    
    Примечание:
        Drift НЕ блокирует торговлю напрямую.
        MetaDecisionBrain использует DriftState как один из факторов.
    """
    # Флаги по типам drift
    confidence_drift: ConfidenceDrift
    entropy_drift: EntropyDrift
    decoupling_drift: DecouplingDrift
    
    # Общий drift
    overall_drift_detected: bool
    overall_severity: DriftSeverity
    overall_reason: str
    
    # Метрики
    metrics: DriftMetrics
    
    # Временные метки
    detection_timestamp: datetime
    recent_window_start: datetime
    recent_window_end: datetime
    baseline_window_start: datetime
    baseline_window_end: datetime
    
    def has_any_drift(self) -> bool:
        """Проверяет, есть ли хотя бы один тип drift"""
        return (
            self.confidence_drift.detected or
            self.entropy_drift.detected or
            self.decoupling_drift.detected or
            self.overall_drift_detected
        )
    
    def get_max_severity(self) -> DriftSeverity:
        """Возвращает максимальный уровень severity"""
        severities = []
        
        if self.confidence_drift.detected:
            severities.append(self.confidence_drift.severity)
        if self.entropy_drift.detected:
            severities.append(self.entropy_drift.severity)
        if self.decoupling_drift.detected:
            severities.append(self.decoupling_drift.severity)
        if self.overall_drift_detected:
            severities.append(self.overall_severity)
        
        if not severities:
            return DriftSeverity.LOW
        
        # Сравниваем по приоритету: HIGH > MEDIUM > LOW
        if DriftSeverity.HIGH in severities:
            return DriftSeverity.HIGH
        elif DriftSeverity.MEDIUM in severities:
            return DriftSeverity.MEDIUM
        else:
            return DriftSeverity.LOW

