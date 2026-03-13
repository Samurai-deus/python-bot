"""
AdaptiveRiskManager — адаптация риска при обнаружении Drift.

Реализует ADR-005: постепенное снижение risk_pct при drift,
восстановление при 24ч отсутствия drift, блокировка при HIGH drift + floor.

Принципы:
    - НЕ меняет SystemState напрямую
    - Возвращает AdaptiveRiskDecision — применение на стороне runner.py
    - НЕ торгует
    - Полностью детерминирован: те же входы → те же решения
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, UTC
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Параметры (ADR-005)
# ---------------------------------------------------------------------------

FLOOR_RISK_PCT: float = 1.5    # % — минимально допустимый риск
STEP_DOWN: float = 0.25        # % — шаг снижения риска за итерацию
STEP_UP: float = 0.25          # % — шаг восстановления риска за итерацию
RECOVERY_HOURS: int = 24       # ч — период без drift для восстановления


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RiskAction(Enum):
    NO_CHANGE = "NO_CHANGE"
    DECREASE = "DECREASE"
    INCREASE = "INCREASE"
    BLOCK_TRADING = "BLOCK_TRADING"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class AdaptiveRiskDecision:
    """
    Решение AdaptiveRiskManager для данной итерации.

    Attrs:
        action:                 Что делать с риском.
        new_risk_pct:           Новый risk_pct (%).
        previous_risk_pct:      Предыдущий risk_pct (%).
        position_size_multiplier: Мультипликатор размера позиции (относительно base).
        block_trading:          True — заблокировать торговлю.
        reason:                 Текстовое объяснение решения.
        drift_severity:         Severity из DriftState (для логирования).
    """
    action: RiskAction
    new_risk_pct: float
    previous_risk_pct: float
    position_size_multiplier: float
    block_trading: bool
    reason: str
    drift_severity: str  # "LOW" | "MEDIUM" | "HIGH" | "NONE"

    @property
    def changed(self) -> bool:
        return abs(self.new_risk_pct - self.previous_risk_pct) > 0.001

    def telegram_message(self) -> Optional[str]:
        """
        Возвращает текст для Telegram уведомления или None, если уведомление не нужно.
        """
        if self.action == RiskAction.NO_CHANGE:
            return None
        if self.block_trading:
            return (
                f"🚨 *DRIFT ALERT — ТОРГОВЛЯ ЗАБЛОКИРОВАНА*\n"
                f"Drift severity: *HIGH*\n"
                f"Риск достиг минимума ({FLOOR_RISK_PCT}%) и drift продолжается.\n"
                f"Причина: {self.reason}"
            )
        if self.action == RiskAction.DECREASE:
            emoji = "⚠️" if self.drift_severity == "MEDIUM" else "🔴"
            return (
                f"{emoji} *Drift обнаружен — снижение риска*\n"
                f"Severity: *{self.drift_severity}*\n"
                f"Риск: `{self.previous_risk_pct:.2f}%` → `{self.new_risk_pct:.2f}%`\n"
                f"Причина: {self.reason}"
            )
        if self.action == RiskAction.INCREASE:
            return (
                f"✅ *Drift устранён — восстановление риска*\n"
                f"Риск: `{self.previous_risk_pct:.2f}%` → `{self.new_risk_pct:.2f}%`"
            )
        return None


# ---------------------------------------------------------------------------
# AdaptiveRiskManager
# ---------------------------------------------------------------------------

class AdaptiveRiskManager:
    """
    Адаптирует risk_pct на основе DriftState.

    Параллельно отслеживает время последнего обнаруженного drift
    для корректного условия восстановления (24ч без drift).

    Использование:
        manager = AdaptiveRiskManager(base_risk_pct=2.0)
        decision = manager.evaluate(drift_state, current_risk_pct=1.75)
    """

    def __init__(self, base_risk_pct: float = 2.0):
        """
        Args:
            base_risk_pct: Базовый риск из config.py (%). Верхняя граница восстановления.
        """
        self.base_risk_pct = base_risk_pct
        self._last_drift_time: Optional[datetime] = None

    def evaluate(
        self,
        drift_state,  # DriftState из core.drift_detector
        current_risk_pct: float,
    ) -> AdaptiveRiskDecision:
        """
        Оценивает DriftState и возвращает решение по риску.

        Args:
            drift_state:       Результат DriftDetector.detect_drift() или None.
            current_risk_pct:  Текущий риск на сделку (%).

        Returns:
            AdaptiveRiskDecision.
        """
        now = datetime.now(UTC)

        # Нет данных о drift (первый запуск или ошибка) — без изменений
        if drift_state is None:
            return self._no_change(current_risk_pct, "No drift data available", "NONE")

        drift_detected = getattr(drift_state, 'overall_drift_detected', False)
        severity_obj = getattr(drift_state, 'overall_severity', None)
        drift_reason = getattr(drift_state, 'overall_reason', '')

        # Определяем severity как строку
        if severity_obj is not None:
            severity = severity_obj.value if hasattr(severity_obj, 'value') else str(severity_obj)
        else:
            severity = 'LOW'

        # ── Drift обнаружен ──────────────────────────────────────────────
        if drift_detected:
            self._last_drift_time = now

            at_floor = current_risk_pct <= FLOOR_RISK_PCT + 0.001

            if severity == 'HIGH' and at_floor:
                # HIGH drift + уже на минимуме → блокировка
                return AdaptiveRiskDecision(
                    action=RiskAction.BLOCK_TRADING,
                    new_risk_pct=FLOOR_RISK_PCT,
                    previous_risk_pct=current_risk_pct,
                    position_size_multiplier=0.0,
                    block_trading=True,
                    reason=drift_reason,
                    drift_severity=severity,
                )

            # LOW drift — снижаем медленнее (полшага)
            step = STEP_DOWN if severity in ('MEDIUM', 'HIGH') else STEP_DOWN * 0.5
            new_risk = max(FLOOR_RISK_PCT, current_risk_pct - step)

            return AdaptiveRiskDecision(
                action=RiskAction.DECREASE,
                new_risk_pct=round(new_risk, 4),
                previous_risk_pct=current_risk_pct,
                position_size_multiplier=round(new_risk / self.base_risk_pct, 4),
                block_trading=False,
                reason=drift_reason,
                drift_severity=severity,
            )

        # ── Drift НЕ обнаружен ───────────────────────────────────────────
        # Проверяем, прошло ли достаточно времени без drift
        recovery_ready = (
            self._last_drift_time is None
            or (now - self._last_drift_time) >= timedelta(hours=RECOVERY_HOURS)
        )

        at_base = current_risk_pct >= self.base_risk_pct - 0.001

        if recovery_ready and not at_base:
            new_risk = min(self.base_risk_pct, current_risk_pct + STEP_UP)
            return AdaptiveRiskDecision(
                action=RiskAction.INCREASE,
                new_risk_pct=round(new_risk, 4),
                previous_risk_pct=current_risk_pct,
                position_size_multiplier=round(new_risk / self.base_risk_pct, 4),
                block_trading=False,
                reason=f"No drift for {RECOVERY_HOURS}h — recovering risk",
                drift_severity="NONE",
            )

        return self._no_change(current_risk_pct, "No drift, risk stable", "NONE")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _no_change(
        self,
        current_risk_pct: float,
        reason: str,
        drift_severity: str,
    ) -> AdaptiveRiskDecision:
        return AdaptiveRiskDecision(
            action=RiskAction.NO_CHANGE,
            new_risk_pct=current_risk_pct,
            previous_risk_pct=current_risk_pct,
            position_size_multiplier=round(current_risk_pct / self.base_risk_pct, 4),
            block_trading=False,
            reason=reason,
            drift_severity=drift_severity,
        )

    def reset(self) -> None:
        """Сбрасывает время последнего drift (для тестирования)."""
        self._last_drift_time = None
