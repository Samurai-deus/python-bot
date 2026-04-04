"""
Decision Core - сердце экосистемы

Собирает состояния всех "умных" ботов, нормализует данные,
выдаёт решение, а не мнение.

Принцип: Decision First - сначала "можно ли", потом "что именно"
"""
from typing import Dict, Optional, List
from datetime import datetime, UTC
from dataclasses import dataclass, asdict
import logging
import os

logger = logging.getLogger(__name__)

# ========== FAULT INJECTION (для тестирования устойчивости) ==========

FAULT_INJECT_DECISION_EXCEPTION = os.environ.get("FAULT_INJECT_DECISION_EXCEPTION", "false").lower() == "true"


@dataclass
class MarketRegime:
    """Состояние рынка от Market Regime Brain"""
    trend_type: str  # "TREND" | "RANGE" | "MIXED" (consumers treat MIXED as RANGE)
    volatility_level: str  # "HIGH" | "MEDIUM" | "LOW"
    risk_sentiment: str  # "RISK_ON" | "RISK_OFF" | "NEUTRAL"
    macro_pressure: Optional[str] = None
    confidence: float = 0.0  # 0.0 - 1.0


@dataclass
class RiskExposure:
    """Риск и экспозиция от Risk & Exposure Brain"""
    total_risk_pct: float  # Суммарный риск в %
    max_correlation: float  # Максимальная корреляция между позициями
    total_leverage: float  # Суммарное плечо
    active_positions: int  # Количество активных позиций
    exposure_pct: float  # Экспозиция в % от депозита
    is_overloaded: bool = False


@dataclass
class CognitiveState:
    """Состояние от Cognitive Filter Bot"""
    overtrading_score: float  # 0.0 - 1.0
    emotional_entries: int  # Количество эмоциональных входов
    fomo_patterns: int  # Количество FOMO паттернов
    recent_trades_count: int  # Количество сделок за период
    should_pause: bool = False


@dataclass
class Opportunity:
    """Возможность от Opportunity Awareness Bot"""
    volatility_squeeze: bool  # Сжатие волатильности
    accumulation: bool  # Накопление
    divergence: bool  # Расхождение ожидание/реакция
    suspicious_silence: bool  # Подозрительная тишина
    readiness_score: float  # 0.0 - 1.0


@dataclass
class TradingDecision:
    """Финальное решение системы"""
    can_trade: bool  # Можно ли торговать
    reason: str  # Причина решения
    risk_level: str  # "LOW" | "MEDIUM" | "HIGH"
    max_position_size: Optional[float] = None  # Максимальный размер позиции
    max_leverage: Optional[float] = None  # Максимальное плечо
    recommendations: List[str] = None  # Рекомендации
    
    def __post_init__(self):
        if self.recommendations is None:
            self.recommendations = []


class DecisionCore:
    """
    Единая точка принятия решений.
    
    Собирает данные от всех "мозгов" и выдаёт одно решение.
    """
    
    def __init__(self):
        """
        Decision Core больше не хранит состояние.
        Состояние хранится в SystemState.
        Decision Core только читает из SystemState и принимает решения.
        """
        self._drawdown_size_multiplier = 1.0

    @property
    def drawdown_size_multiplier(self) -> float:
        """Returns position size multiplier based on current drawdown level.

        1.0 = no drawdown reduction
        0.5 = 10%+ drawdown, reduce size by 50%
        0.25 = 15%+ drawdown, reduce size by 75%
        0.0 = 20%+ drawdown, trading halted (24h pause)
        25%+ drawdown = trading halted, requires manual reset
        """
        return self._drawdown_size_multiplier
    
    def reset(self):
        """
        Сбрасывает состояние Decision Core (теперь не нужно - состояние в SystemState).
        Оставлено для обратной совместимости.
        """
        self._drawdown_size_multiplier = 1.0
    
    def should_i_trade(self, symbol: Optional[str] = None, system_state=None) -> TradingDecision:
        """
        Главный вопрос: можно ли торговать?
        
        Args:
            symbol: Символ для проверки (опционально)
            
        Returns:
            TradingDecision: Решение системы
        """
        try:
            reasons = []
            can_trade = True
            risk_level = "LOW"
            max_position_size = None
            max_leverage = None
            _RISK_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
            recommendations = []
            
            # Graduated drawdown response
            try:
                from capital import current_drawdown_pct
                dd = current_drawdown_pct()
                if dd >= 25.0:
                    return TradingDecision(
                        can_trade=False,
                        reason=f"DRAWDOWN BREAKER: просадка {dd:.1f}% >= 25% — требуется ручной сброс",
                        risk_level="HIGH",
                        recommendations=["Торговля остановлена до ручного сброса"]
                    )
                elif dd >= 20.0:
                    # 20%+ drawdown: halt for 24h then auto-resume at 50% size
                    # Store multiplier for consumers; trading halted
                    self._drawdown_size_multiplier = 0.0
                    return TradingDecision(
                        can_trade=False,
                        reason=f"DRAWDOWN BREAKER: просадка {dd:.1f}% >= 20% — торговля приостановлена на 24ч",
                        risk_level="HIGH",
                        recommendations=[
                            "Торговля приостановлена на 24 часа",
                            "После возобновления размер позиций = 50%"
                        ]
                    )
                elif dd >= 15.0:
                    self._drawdown_size_multiplier = 0.25
                    recommendations.append(f"Просадка {dd:.1f}%: размер позиций снижен на 75%")
                    risk_level = "HIGH"
                elif dd >= 10.0:
                    self._drawdown_size_multiplier = 0.5
                    recommendations.append(f"Просадка {dd:.1f}%: размер позиций снижен на 50%")
                    if _RISK_ORDER.get("MEDIUM", 1) > _RISK_ORDER.get(risk_level, 0):
                        risk_level = "MEDIUM"
                else:
                    self._drawdown_size_multiplier = 1.0
            except Exception as e:
                logger.error("Drawdown check failed: %s — blocking trade (fail-closed)", e)
                return TradingDecision(
                    can_trade=False,
                    reason=f"DRAWDOWN CHECK FAILED: {e}",
                    risk_level="HIGH",
                    recommendations=["Drawdown calculation error — fail-closed"]
                )

            # Проверка safe-mode (критично - блокирует торговлю при ошибках)
            if system_state and system_state.system_health.safe_mode:
                return TradingDecision(
                    can_trade=False,
                    reason="SAFE-MODE: система в режиме безопасности",
                    risk_level="HIGH",
                    recommendations=["Проверьте логи", "Дождитесь восстановления системы"]
                )
            
            # Читаем состояние из SystemState (ИНВАРИАНТ: DecisionCore не хранит состояние)
            cognitive_state = system_state.cognitive_state if system_state else None
            market_regime = system_state.market_regime if system_state else None
            risk_exposure = system_state.risk_state if system_state else None
            opportunities = system_state.opportunities if system_state else {}
            
            # 1. Проверка когнитивного фильтра (самый важный)
            if cognitive_state:
                if cognitive_state.should_pause:
                    can_trade = False
                    reasons.append("⏸ Когнитивный фильтр: требуется пауза")
                    decision = TradingDecision(
                        can_trade=False,
                        reason="Когнитивный фильтр блокирует торговлю",
                        risk_level="HIGH",
                        recommendations=["Сделайте паузу", "Проверьте эмоциональное состояние"]
                    )
                    # Обновляем решение в SystemState
                    if system_state:
                        system_state.update_trading_decision(False)
                    return decision
                
                if cognitive_state.overtrading_score > 0.7:
                    can_trade = False
                    reasons.append("⚠️ Пере-торговля обнаружена")
                    return TradingDecision(
                        can_trade=False,
                        reason="Обнаружена пере-торговля",
                        risk_level="HIGH",
                        recommendations=["Уменьшите частоту входов", "Соблюдайте дисциплину"]
                    )
            
            # 2. Проверка риска и экспозиции
            if risk_exposure:
                if risk_exposure.is_overloaded:
                    can_trade = False
                    reasons.append("🚨 Перегрузка: превышен лимит риска/экспозиции")
                    return TradingDecision(
                        can_trade=False,
                        reason="Превышен лимит риска или экспозиции",
                        risk_level="HIGH",
                        recommendations=[
                            f"Текущий риск: {risk_exposure.total_risk_pct:.1f}%",
                            f"Активных позиций: {risk_exposure.active_positions}",
                            "Закройте часть позиций перед новыми входами"
                        ]
                    )
                
                if risk_exposure.total_risk_pct > 10.0:
                    risk_level = "HIGH"
                    reasons.append(f"⚠️ Высокий суммарный риск: {risk_exposure.total_risk_pct:.1f}%")
                elif risk_exposure.total_risk_pct > 5.0:
                    risk_level = "MEDIUM"
                    reasons.append(f"📊 Средний риск: {risk_exposure.total_risk_pct:.1f}%")
            
            # 3. Проверка режима рынка
            if market_regime:
                if market_regime.risk_sentiment == "RISK_OFF":
                    if _RISK_ORDER.get("MEDIUM", 1) > _RISK_ORDER.get(risk_level, 0):
                        risk_level = "MEDIUM"
                    reasons.append("📉 Режим RISK-OFF: повышенная осторожность")

                if market_regime.volatility_level == "HIGH":
                    if _RISK_ORDER.get("MEDIUM", 1) > _RISK_ORDER.get(risk_level, 0):
                        risk_level = "MEDIUM"
                    reasons.append("📊 Высокая волатильность: уменьшите размер позиций")
            
            # 4. Проверка возможностей (если указан символ)
            if symbol and symbol in opportunities:
                opp = opportunities[symbol]
                if opp.readiness_score < 0.3:
                    # Только рекомендация — не блокировка.
                    # В RISK_OFF / нисходящем тренде readiness_score=0.0 для всех символов
                    # (нет squeeze/accumulation паттернов), но SHORT сигналы по-прежнему валидны.
                    recommendations.append(f"⏸ Низкая готовность для {symbol}: {opp.readiness_score:.1%}")
            
            # 5. Расчет максимальных параметров
            max_position_size = None
            max_leverage = None
            
            if risk_exposure and can_trade:
                # max_position_size намеренно None — размер позиции определяет PositionSizer,
                # а не DecisionCore. Старая формула remaining_risk * 100 давала max=$990
                # при любой открытой позиции, блокируя все новые сигналы.
                max_position_size = None

                # Максимальное плечо зависит от риска
                if risk_level == "HIGH":
                    max_leverage = 2.0
                elif risk_level == "MEDIUM":
                    max_leverage = 5.0
                else:
                    max_leverage = 10.0
            
            # Формируем рекомендации (recommendations инициализирован выше, не сбрасываем)
            if market_regime:
                if market_regime.trend_type == "RANGE":
                    recommendations.append("Рынок в диапазоне: используйте range-стратегии")
                elif market_regime.trend_type == "TREND":
                    recommendations.append("Рынок в тренде: следуйте тренду")
            
            if risk_exposure:
                if risk_exposure.max_correlation > 0.8:
                    recommendations.append("⚠️ Высокая корреляция между позициями: диверсифицируйте")
            
            if not reasons:
                reasons.append("✅ Все системы в норме")
            
            decision = TradingDecision(
                can_trade=can_trade,
                reason="; ".join(reasons),
                risk_level=risk_level,
                max_position_size=max_position_size,
                max_leverage=max_leverage,
                recommendations=recommendations
            )
            
            # ========== FAULT INJECTION (контролируемая) ==========
            # Поднимаем исключение ПОСЛЕ создания snapshot (в signal_generator),
            # но ДО side effects (update_trading_decision)
            if FAULT_INJECT_DECISION_EXCEPTION:
                raise RuntimeError(
                    "FAULT_INJECTION: decision_exception - "
                    "Controlled fault injection for runtime resilience testing. "
                    "This exception is expected when FAULT_INJECT_DECISION_EXCEPTION=true"
                )
            
            # Обновляем решение в SystemState (side effect - выполняется только если fault injection не активен)
            if system_state:
                system_state.update_trading_decision(can_trade)
            
            return decision
        except Exception as e:
            # Критическая ошибка - блокируем торговлю
            logger.error("Критическая ошибка в Decision Core.should_i_trade: %s: %s", type(e).__name__, e, exc_info=True)
            return TradingDecision(
                can_trade=False,
                reason=f"Критическая ошибка в системе принятия решений: {type(e).__name__}",
                risk_level="HIGH",
                recommendations=["Система временно недоступна", "Проверьте логи для деталей"]
            )
    
    def get_risk_status(self, system_state=None) -> Dict:
        """
        Получить статус риска.
        
        ИНВАРИАНТ: Читает из SystemState, не из self.
        """
        try:
            # Читаем из SystemState
            risk_exposure = system_state.risk_state if system_state else None
            
            status = {
                "timestamp": datetime.now(UTC).isoformat(),
                "can_trade": False,
                "total_risk_pct": 0.0,
                "active_positions": 0,
                "warnings": []
            }
            
            if risk_exposure:
                status["total_risk_pct"] = risk_exposure.total_risk_pct
                status["active_positions"] = risk_exposure.active_positions
                status["exposure_pct"] = risk_exposure.exposure_pct
                status["total_leverage"] = risk_exposure.total_leverage
                
                if risk_exposure.is_overloaded:
                    status["warnings"].append("Перегрузка: превышен лимит")
            
            decision = self.should_i_trade(system_state=system_state)
            status["can_trade"] = decision.can_trade
            status["risk_level"] = decision.risk_level
            
            return status
        except Exception as e:
            logger.error("Ошибка в Decision Core.get_risk_status: %s: %s", type(e).__name__, e, exc_info=True)
            return {
                "timestamp": datetime.now(UTC).isoformat(),
                "can_trade": False,
                "total_risk_pct": 0.0,
                "active_positions": 0,
                "warnings": [f"Ошибка получения статуса: {type(e).__name__}"],
                "risk_level": "HIGH"
            }
    
    def get_full_context(self, system_state=None) -> Dict:
        """
        Получить полный контекст для отладки.
        
        ИНВАРИАНТ: Читает из SystemState, не из self.
        """
        try:
            # Читаем из SystemState
            market_regime = system_state.market_regime if system_state else None
            risk_exposure = system_state.risk_state if system_state else None
            cognitive_state = system_state.cognitive_state if system_state else None
            opportunities = system_state.opportunities if system_state else {}
            
            return {
                "market_regime": asdict(market_regime) if market_regime else None,
                "risk_exposure": asdict(risk_exposure) if risk_exposure else None,
                "cognitive_state": asdict(cognitive_state) if cognitive_state else None,
                "opportunities": {k: asdict(v) for k, v in opportunities.items()},
                "decision": asdict(self.should_i_trade(system_state=system_state))
            }
        except Exception as e:
            logger.error("Ошибка в Decision Core.get_full_context: %s: %s", type(e).__name__, e, exc_info=True)
            return {
                "error": f"Ошибка получения контекста: {type(e).__name__}: {str(e)}",
                "market_regime": None,
                "risk_exposure": None,
                "cognitive_state": None,
                "opportunities": {},
                "decision": None
            }


# Глобальный экземпляр Decision Core
_decision_core = None

def get_decision_core() -> DecisionCore:
    """Получить глобальный экземпляр Decision Core"""
    global _decision_core
    if _decision_core is None:
        _decision_core = DecisionCore()
    return _decision_core

