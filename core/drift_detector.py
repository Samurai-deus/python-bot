"""
Drift Detector - выявление деградации поведения системы.

Drift Detector анализирует entropy и confidence во времени
для выявления деградации поведения системы.

ОБЩИЕ ПРАВИЛА:
- Drift Detector НЕ торгует
- НЕ использует рынок или индикаторы
- Работает ТОЛЬКО на SignalSnapshot
- Не изменяет SystemState напрямую

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
from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from typing import List, Optional
import logging

from core.signal_snapshot_store import SignalSnapshotRecord
from core.drift_models import (
    DriftState, DriftSeverity, ConfidenceDrift, EntropyDrift, DecouplingDrift, DriftMetrics
)
from core.drift_metrics import calculate_drift_metrics

logger = logging.getLogger(__name__)


class DriftDetector:
    """
    Детектор деградации поведения системы.
    
    Анализирует entropy и confidence во времени для выявления drift.
    
    Принципы:
    - НЕ торгует
    - НЕ использует рынок или индикаторы
    - Работает ТОЛЬКО на SignalSnapshot
    - Не изменяет SystemState напрямую
    """
    
    def __init__(
        self,
        recent_window_hours: int = 24,
        baseline_window_hours: int = 168  # 7 дней
    ):
        """
        Инициализация DriftDetector.
        
        Args:
            recent_window_hours: Размер recent окна в часах (по умолчанию 24)
            baseline_window_hours: Размер baseline окна в часах (по умолчанию 168 = 7 дней)
        """
        self.recent_window_hours = recent_window_hours
        self.baseline_window_hours = baseline_window_hours
    
    def detect_drift(
        self,
        snapshots: List[SignalSnapshotRecord],
        end_time: Optional[datetime] = None
    ) -> Optional[DriftState]:
        """
        Выявляет drift в списке snapshot'ов.
        
        Args:
            snapshots: Список SignalSnapshotRecord
            end_time: Конец recent окна (по умолчанию - текущее время)
        
        Returns:
            DriftState или None если недостаточно данных
        """
        if not snapshots:
            logger.warning("No snapshots provided for drift detection")
            return None
        
        if end_time is None:
            end_time = datetime.now(UTC)
        
        # Разделяем на окна
        recent_snapshots, baseline_snapshots = self._split_windows(snapshots, end_time)
        
        if not recent_snapshots or not baseline_snapshots:
            logger.warning("Insufficient data for drift detection")
            return None
        
        # Извлекаем значения
        recent_confidence, recent_entropy = self._extract_values(recent_snapshots)
        baseline_confidence, baseline_entropy = self._extract_values(baseline_snapshots)
        
        # Вычисляем метрики
        metrics = self._calculate_metrics(
            recent_confidence, recent_entropy,
            baseline_confidence, baseline_entropy,
            len(recent_snapshots), len(baseline_snapshots)
        )
        
        # Обнаруживаем drift
        confidence_drift = self.detect_confidence_drift(metrics)
        entropy_drift = self.detect_entropy_drift(metrics)
        decoupling_drift = self.detect_decoupling_drift(metrics)
        
        # Общий drift
        overall_drift, overall_severity, overall_reason = self.compute_overall_drift(
            confidence_drift, entropy_drift, decoupling_drift
        )
        
        # Временные метки
        recent_start = recent_snapshots[-1].timestamp if recent_snapshots else end_time
        recent_end = recent_snapshots[0].timestamp if recent_snapshots else end_time
        baseline_start = baseline_snapshots[-1].timestamp if baseline_snapshots else end_time
        baseline_end = baseline_snapshots[0].timestamp if baseline_snapshots else end_time
        
        return DriftState(
            confidence_drift=confidence_drift,
            entropy_drift=entropy_drift,
            decoupling_drift=decoupling_drift,
            overall_drift_detected=overall_drift,
            overall_severity=overall_severity,
            overall_reason=overall_reason,
            metrics=metrics,
            detection_timestamp=end_time,
            recent_window_start=recent_start,
            recent_window_end=recent_end,
            baseline_window_start=baseline_start,
            baseline_window_end=baseline_end
        )
    
    def detect_confidence_drift(self, metrics: DriftMetrics) -> ConfidenceDrift:
        """
        Выявляет drift в confidence.
        
        Args:
            metrics: DriftMetrics с вычисленными метриками
        
        Returns:
            ConfidenceDrift: Результат обнаружения drift
        """
        # Вычисляем разницы
        mean_diff = metrics.confidence_mean_recent - metrics.confidence_mean_baseline
        mean_diff_pct = abs(mean_diff / metrics.confidence_mean_baseline) if metrics.confidence_mean_baseline > 0 else 0.0
        
        variance_diff = abs(metrics.confidence_variance_recent - metrics.confidence_variance_baseline)
        variance_diff_pct = variance_diff / metrics.confidence_variance_baseline if metrics.confidence_variance_baseline > 0 else 0.0
        
        percentile_shift = abs(metrics.confidence_p90_recent - metrics.confidence_p90_baseline)
        
        # Определяем severity
        detected = False
        severity = DriftSeverity.LOW
        reasons = []
        
        if mean_diff_pct > 0.25:  # 25% изменение среднего
            detected = True
            severity = DriftSeverity.HIGH
            reasons.append(f"Mean confidence changed by {mean_diff_pct * 100:.1f}%")
        elif mean_diff_pct > 0.15:  # 15% изменение среднего
            detected = True
            severity = DriftSeverity.MEDIUM
            reasons.append(f"Mean confidence changed by {mean_diff_pct * 100:.1f}%")
        elif mean_diff_pct > 0.10:  # 10% изменение среднего
            detected = True
            severity = DriftSeverity.LOW
            reasons.append(f"Mean confidence changed by {mean_diff_pct * 100:.1f}%")
        
        if variance_diff_pct > 0.5:  # 50% изменение дисперсии
            detected = True
            if severity == DriftSeverity.LOW:
                severity = DriftSeverity.MEDIUM
            reasons.append(f"Confidence variance changed by {variance_diff_pct * 100:.1f}%")
        
        if percentile_shift > 0.15:  # Сдвиг перцентиля
            detected = True
            if severity == DriftSeverity.LOW:
                severity = DriftSeverity.MEDIUM
            reasons.append(f"Confidence percentile shifted by {percentile_shift:.3f}")
        
        reason = "; ".join(reasons) if reasons else "No confidence drift detected"
        
        return ConfidenceDrift(
            detected=detected,
            severity=severity,
            reason=reason,
            mean_diff=mean_diff,
            mean_diff_pct=mean_diff_pct,
            variance_diff=variance_diff,
            percentile_shift=percentile_shift
        )
    
    def detect_entropy_drift(self, metrics: DriftMetrics) -> EntropyDrift:
        """
        Выявляет drift в entropy.
        
        Args:
            metrics: DriftMetrics с вычисленными метриками
        
        Returns:
            EntropyDrift: Результат обнаружения drift
        """
        # Вычисляем разницы
        mean_diff = metrics.entropy_mean_recent - metrics.entropy_mean_baseline
        mean_diff_pct = abs(mean_diff / metrics.entropy_mean_baseline) if metrics.entropy_mean_baseline > 0 else 0.0
        
        variance_diff = abs(metrics.entropy_variance_recent - metrics.entropy_variance_baseline)
        variance_diff_pct = variance_diff / metrics.entropy_variance_baseline if metrics.entropy_variance_baseline > 0 else 0.0
        
        percentile_shift = abs(metrics.entropy_p90_recent - metrics.entropy_p90_baseline)
        
        # Определяем severity
        detected = False
        severity = DriftSeverity.LOW
        reasons = []
        
        if mean_diff_pct > 0.25:  # 25% изменение среднего
            detected = True
            severity = DriftSeverity.HIGH
            reasons.append(f"Mean entropy changed by {mean_diff_pct * 100:.1f}%")
        elif mean_diff_pct > 0.15:  # 15% изменение среднего
            detected = True
            severity = DriftSeverity.MEDIUM
            reasons.append(f"Mean entropy changed by {mean_diff_pct * 100:.1f}%")
        elif mean_diff_pct > 0.10:  # 10% изменение среднего
            detected = True
            severity = DriftSeverity.LOW
            reasons.append(f"Mean entropy changed by {mean_diff_pct * 100:.1f}%")
        
        if variance_diff_pct > 0.5:  # 50% изменение дисперсии
            detected = True
            if severity == DriftSeverity.LOW:
                severity = DriftSeverity.MEDIUM
            reasons.append(f"Entropy variance changed by {variance_diff_pct * 100:.1f}%")
        
        if percentile_shift > 0.15:  # Сдвиг перцентиля
            detected = True
            if severity == DriftSeverity.LOW:
                severity = DriftSeverity.MEDIUM
            reasons.append(f"Entropy percentile shifted by {percentile_shift:.3f}")
        
        reason = "; ".join(reasons) if reasons else "No entropy drift detected"
        
        return EntropyDrift(
            detected=detected,
            severity=severity,
            reason=reason,
            mean_diff=mean_diff,
            mean_diff_pct=mean_diff_pct,
            variance_diff=variance_diff,
            percentile_shift=percentile_shift
        )
    
    def detect_decoupling_drift(self, metrics: DriftMetrics) -> DecouplingDrift:
        """
        Выявляет рассогласование (decoupling) между confidence и entropy.
        
        Args:
            metrics: DriftMetrics с вычисленными метриками
        
        Returns:
            DecouplingDrift: Результат обнаружения drift
        """
        correlation_diff = abs(metrics.correlation_recent - metrics.correlation_baseline)
        
        # Определяем severity
        detected = False
        severity = DriftSeverity.LOW
        reason = ""
        
        if correlation_diff > 0.4:  # Изменение корреляции на 40%
            detected = True
            severity = DriftSeverity.HIGH
            reason = f"Correlation changed by {correlation_diff:.3f} (recent: {metrics.correlation_recent:.3f}, baseline: {metrics.correlation_baseline:.3f})"
        elif correlation_diff > 0.3:  # Изменение корреляции на 30%
            detected = True
            severity = DriftSeverity.MEDIUM
            reason = f"Correlation changed by {correlation_diff:.3f} (recent: {metrics.correlation_recent:.3f}, baseline: {metrics.correlation_baseline:.3f})"
        elif correlation_diff > 0.2:  # Изменение корреляции на 20%
            detected = True
            severity = DriftSeverity.LOW
            reason = f"Correlation changed by {correlation_diff:.3f} (recent: {metrics.correlation_recent:.3f}, baseline: {metrics.correlation_baseline:.3f})"
        else:
            reason = "No decoupling drift detected"
        
        return DecouplingDrift(
            detected=detected,
            severity=severity,
            reason=reason,
            correlation_diff=correlation_diff,
            correlation_recent=metrics.correlation_recent,
            correlation_baseline=metrics.correlation_baseline
        )
    
    def compute_overall_drift(
        self,
        confidence_drift: ConfidenceDrift,
        entropy_drift: EntropyDrift,
        decoupling_drift: DecouplingDrift
    ) -> tuple[bool, DriftSeverity, str]:
        """
        Вычисляет общий drift на основе всех типов drift.
        
        Args:
            confidence_drift: Drift в confidence
            entropy_drift: Drift в entropy
            decoupling_drift: Drift в decoupling
        
        Returns:
            Tuple: (detected, severity, reason)
        """
        detected = (
            confidence_drift.detected or
            entropy_drift.detected or
            decoupling_drift.detected
        )
        
        if not detected:
            return False, DriftSeverity.LOW, "No drift detected"
        
        # Определяем максимальный severity
        severities = []
        reasons = []
        
        if confidence_drift.detected:
            severities.append(confidence_drift.severity)
            reasons.append(f"Confidence: {confidence_drift.reason}")
        
        if entropy_drift.detected:
            severities.append(entropy_drift.severity)
            reasons.append(f"Entropy: {entropy_drift.reason}")
        
        if decoupling_drift.detected:
            severities.append(decoupling_drift.severity)
            reasons.append(f"Decoupling: {decoupling_drift.reason}")
        
        # Максимальный severity
        if DriftSeverity.HIGH in severities:
            severity = DriftSeverity.HIGH
        elif DriftSeverity.MEDIUM in severities:
            severity = DriftSeverity.MEDIUM
        else:
            severity = DriftSeverity.LOW
        
        reason = "; ".join(reasons)
        
        return detected, severity, reason
    
    def _split_windows(
        self,
        snapshots: List[SignalSnapshotRecord],
        end_time: datetime
    ) -> tuple[List[SignalSnapshotRecord], List[SignalSnapshotRecord]]:
        """
        Разделяет snapshot'ы на recent и baseline окна.
        
        Args:
            snapshots: Список snapshot'ов (должен быть отсортирован по timestamp DESC)
            end_time: Конец recent окна
        
        Returns:
            Tuple: (recent_snapshots, baseline_snapshots)
        """
        # Сортируем по timestamp (DESC)
        sorted_snapshots = sorted(snapshots, key=lambda s: s.timestamp, reverse=True)
        
        # Определяем границы окон
        recent_start = end_time - timedelta(hours=self.recent_window_hours)
        baseline_start = recent_start - timedelta(hours=self.baseline_window_hours)
        baseline_end = recent_start
        
        # Разделяем
        recent_snapshots = [
            s for s in sorted_snapshots
            if recent_start <= s.timestamp <= end_time
        ]
        
        baseline_snapshots = [
            s for s in sorted_snapshots
            if baseline_start <= s.timestamp < baseline_end
        ]
        
        return recent_snapshots, baseline_snapshots
    
    def _extract_values(
        self,
        snapshots: List[SignalSnapshotRecord]
    ) -> tuple[List[float], List[float]]:
        """
        Извлекает confidence и entropy из snapshot'ов.
        
        Args:
            snapshots: Список snapshot'ов
        
        Returns:
            Tuple: (confidence_values, entropy_values)
        """
        confidence_values = [s.confidence for s in snapshots]
        entropy_values = [s.entropy for s in snapshots]
        
        return confidence_values, entropy_values
    
    def _calculate_metrics(
        self,
        recent_confidence: List[float],
        recent_entropy: List[float],
        baseline_confidence: List[float],
        baseline_entropy: List[float],
        recent_size: int,
        baseline_size: int
    ) -> DriftMetrics:
        """
        Вычисляет метрики для drift detection.
        
        Args:
            recent_confidence: Confidence в recent окне
            recent_entropy: Entropy в recent окне
            baseline_confidence: Confidence в baseline окне
            baseline_entropy: Entropy в baseline окне
            recent_size: Размер recent окна
            baseline_size: Размер baseline окна
        
        Returns:
            DriftMetrics: Вычисленные метрики
        """
        (
            conf_mean_recent, conf_mean_baseline,
            conf_var_recent, conf_var_baseline,
            conf_p90_recent, conf_p95_recent,
            conf_p90_baseline, conf_p95_baseline,
            ent_mean_recent, ent_mean_baseline,
            ent_var_recent, ent_var_baseline,
            ent_p90_recent, ent_p95_recent,
            ent_p90_baseline, ent_p95_baseline,
            corr_recent, corr_baseline
        ) = calculate_drift_metrics(
            recent_confidence, recent_entropy,
            baseline_confidence, baseline_entropy
        )
        
        return DriftMetrics(
            confidence_mean_recent=conf_mean_recent,
            confidence_mean_baseline=conf_mean_baseline,
            confidence_variance_recent=conf_var_recent,
            confidence_variance_baseline=conf_var_baseline,
            confidence_p90_recent=conf_p90_recent,
            confidence_p95_recent=conf_p95_recent,
            confidence_p90_baseline=conf_p90_baseline,
            confidence_p95_baseline=conf_p95_baseline,
            entropy_mean_recent=ent_mean_recent,
            entropy_mean_baseline=ent_mean_baseline,
            entropy_variance_recent=ent_var_recent,
            entropy_variance_baseline=ent_var_baseline,
            entropy_p90_recent=ent_p90_recent,
            entropy_p95_recent=ent_p95_recent,
            entropy_p90_baseline=ent_p90_baseline,
            entropy_p95_baseline=ent_p95_baseline,
            correlation_recent=corr_recent,
            correlation_baseline=corr_baseline,
            recent_window_size=recent_size,
            baseline_window_size=baseline_size
        )

