"""
Gatekeeper - между сигналами и пользователем

Проверяет сигналы через Decision Core и Portfolio Brain перед отправкой пользователю.
"""
from typing import Dict, Optional, List
from trading_mode import get_trading_mode, TradingMode
from core.decision_core import get_decision_core, TradingDecision
from core.portfolio_brain import (
    get_portfolio_brain, PortfolioBrain, PortfolioAnalysis,
    convert_trades_to_positions, calculate_portfolio_state,
    PortfolioDecision, PortfolioState
)
from core.signal_snapshot import SignalSnapshot
from core.system_guardian import get_system_guardian
from core.risk_core import (
    get_risk_core, TradingIntent, CapitalSnapshot, ExposureSnapshot,
    PositionSnapshot, BehavioralCounters, SystemHealthFlags,
    TradingPermission
)
from trade_manager import get_open_trades
from capital import get_current_balance, INITIAL_BALANCE, RISK_PERCENT
from telegram_bot import send_message, send_chart
from datetime import datetime, UTC, timedelta
from bot_statistics import get_trade_statistics
import logging

logger = logging.getLogger(__name__)
from signals import build_signal

# MetaDecisionBrain - обязательный импорт (ADR-004: fail-closed, INV-5)
from brains.meta_decision_brain import (
    MetaDecisionBrain, MetaDecisionResult, BlockLevel as MetaBlockLevel,
    SystemHealthStatus, TimeContext
)

# DecisionTrace - опциональный импорт (для объяснимости решений, не влияет на решение)
try:
    from core.decision_trace import DecisionTrace, BlockLevel as TraceBlockLevel
    DECISION_TRACE_AVAILABLE = True
except ImportError:
    DECISION_TRACE_AVAILABLE = False
    DecisionTrace = None
    TraceBlockLevel = None

# PositionSizer - обязательный импорт (ADR-004: fail-closed, INV-5)
from core.position_sizer import PositionSizer, PortfolioStateAdapter


class Gatekeeper:
    """
    Gatekeeper проверяет все сигналы через Decision Core.
    
    Только сигналы, прошедшие проверку, доходят до пользователя.
    """
    
    def __init__(self):
        self.decision_core = get_decision_core()
        self.portfolio_brain = get_portfolio_brain()
        # Risk Core - обязательный модуль (ADR-TRADING-RISK-CORE-001)
        self.risk_core = get_risk_core()
        # MetaDecisionBrain - обязательный модуль (ADR-004: fail-closed, INV-5)
        self.meta_decision_brain = MetaDecisionBrain()
        # DecisionTrace - опционально (для объяснимости решений, не влияет на решение)
        self.decision_trace = None
        self.trace_enabled = False
        if DECISION_TRACE_AVAILABLE:
            try:
                self.decision_trace = DecisionTrace()
                self.trace_enabled = True
            except Exception as e:
                logger.warning(f"DecisionTrace недоступен: {type(e).__name__}: {e}")
                self.decision_trace = None
                self.trace_enabled = False
        # PositionSizer - обязательный модуль (ADR-004: fail-closed, INV-5)
        self.position_sizer = PositionSizer()
        self.blocked_signals_count = 0
        self.approved_signals_count = 0
        # Явное состояние (статистика)
        self.state = {
            "blocked": 0,
            "approved": 0,
            "total": 0
        }
    
    def reset(self):
        """
        Сбрасывает состояние Gatekeeper.
        Полезно для тестирования и перезапуска анализа.
        """
        self.blocked_signals_count = 0
        self.approved_signals_count = 0
        self.state = {
            "blocked": 0,
            "approved": 0,
            "total": 0
        }
    
    def check_signal(self, symbol: str, signal_data: Dict, system_state=None) -> bool:
        """
        Проверяет сигнал через Decision Core.
        
        Args:
            symbol: Торговая пара
            signal_data: Данные сигнала (zone, risk, и т.д.)
            system_state: Состояние системы (опционально)
            
        Returns:
            bool: True если сигнал одобрен, False если заблокирован
        """
        try:
            # Получаем system_state если не передан
            if system_state is None:
                from system_state import get_system_state
                system_state = get_system_state()
            
            # Получаем решение от Decision Core
            decision = self.decision_core.should_i_trade(symbol=symbol, system_state=system_state)
            
            if not decision.can_trade:
                self.blocked_signals_count += 1
                self._update_state()
                self._log_blocked_signal(symbol, decision)
                return False
            
            # Дополнительные проверки
            if not self._check_signal_quality(signal_data, decision):
                self.blocked_signals_count += 1
                self._update_state()
                logger.debug(f"Gatekeeper: сигнал {symbol} заблокирован из-за качества (размер или плечо)")
                return False
            
            self.approved_signals_count += 1
            self._update_state()
            return True
        except Exception as e:
            # Критическая ошибка - блокируем сигнал для безопасности
            logger.error(f"Критическая ошибка в Gatekeeper.check_signal для {symbol}: {type(e).__name__}: {e}", exc_info=True)
            self.blocked_signals_count += 1
            self._update_state()
            return False
    
    def send_signal(self, symbol: str, signal_data: Dict, 
                   states: Dict, directions: Dict, 
                   risk: str, score: int, mode: str, reasons: list,
                   system_state=None, snapshot: Optional[SignalSnapshot] = None):
        """
        Отправляет сигнал пользователю (если прошел проверку).
        
        Args:
            symbol: Торговая пара
            signal_data: Данные сигнала
            states: Состояния по таймфреймам
            directions: Направления
            risk: Уровень риска
            score: Score сигнала
            mode: Режим рынка
            reasons: Причины сигнала
            system_state: Состояние системы (опционально)
            snapshot: SignalSnapshot (опционально, для портфельного анализа)
        """
        try:
            # Получаем system_state если не передан
            if system_state is None:
                from system_state import get_system_state
                system_state = get_system_state()
            
            # ========== SYSTEM GUARDIAN - ОБЯЗАТЕЛЬНЫЙ ГЛОБАЛЬНЫЙ БАРЬЕР ==========
            # АРХИТЕКТУРНЫЙ ИНВАРИАНТ: Невозможно отправить сигнал без прохождения SystemGuardian
            # SystemGuardian - абсолютный системный барьер перед торговлей
            # Проверяет все инварианты и здоровье CRITICAL модулей
            # CRITICAL: любой сбой → блокировка торговли (fail-safe)
            # 
            # КОНТРАКТ:
            # - Gatekeeper вызывает ТОЛЬКО синхронный метод can_trade_sync()
            # - Вся async логика инкапсулирована внутри SystemGuardian
            # - Gatekeeper не знает об async деталях (детерминированный, контекстно-независимый)
            # 
            # ЗАПРЕЩЕНО:
            # - Обходить SystemGuardian
            # - Вызывать async методы SystemGuardian напрямую
            # - Отправлять сигналы без проверки SystemGuardian
            system_guardian = get_system_guardian()
            permission = system_guardian.can_trade_sync()
            
            # АРХИТЕКТУРНОЕ ПРИНУЖДЕНИЕ: Если SystemGuardian блокирует → немедленный выход
            if not permission.allowed:
                logger.warning(
                    f"Signal blocked by SystemGuardian for {symbol}: {permission.reason} "
                    f"(blocked_by: {permission.blocked_by})"
                )
                self.blocked_signals_count += 1
                self._update_state()
                return  # Early exit - fail-safe (архитектурно принудительно)
            
            # ========== DECISION TRACE - ЛОКАЛЬНЫЙ СБОР РЕШЕНИЙ ==========
            # Создаём локальный trace для этого сигнала (не влияет на runtime)
            trace_entries = []  # Список решений для логирования
            
            # ========== RISK CORE - ОБЯЗАТЕЛЬНЫЙ ФИЛЬТР (VETO POWER) ==========
            # ADR-TRADING-RISK-CORE-001: Risk Core имеет право вето
            # ADR-TRADING-RISK-CORE-001 Section 6: If Risk Core fails → DENY
            # Risk Core проверяется ПОСЛЕ SystemGuardian, но ПЕРЕД DecisionCore
            # Risk Core всегда fail-closed - при неопределенности запрещает торговлю
            # FAIL-CLOSED ENFORCEMENT: Risk Core evaluation is MANDATORY and AUTHORITATIVE
            # Any failure (exception, None, malformed result) → DENY + HALTED immediately
            try:
                risk_core_result = self._check_risk_core(symbol, signal_data, system_state)
                
                # FAIL-CLOSED: If Risk Core returns None or malformed result → DENY + HALTED
                if not risk_core_result:
                    logger.critical(
                        f"Risk Core evaluation failed for {symbol}: returned None. "
                        f"ADR-TRADING-RISK-CORE-001 violation: enforcing DENY + HALTED"
                    )
                    # Treat as DENY + HALTED (fail-closed)
                    from core.risk_core import RiskState
                    risk_reason = "Risk Core evaluation failed (returned None) → DENY + HALTED"
                    block_level = TraceBlockLevel.HARD if TraceBlockLevel else None
                    trace_entries.append(("RiskCore", False, risk_reason, block_level))
                    logger.error(f"[TRACE] RiskCore → DENY → {risk_reason}")
                    print(f"   🚫 Risk Core evaluation failed for {symbol}: enforcing DENY + HALTED")
                    self.blocked_signals_count += 1
                    self._update_state()
                    self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                    return  # Early exit - fail-closed enforcement
                
                # Validate result structure (fail-closed)
                if not isinstance(risk_core_result, tuple) or len(risk_core_result) != 3:
                    logger.critical(
                        f"Risk Core evaluation returned malformed result for {symbol}: {type(risk_core_result)}. "
                        f"ADR-TRADING-RISK-CORE-001 violation: enforcing DENY + HALTED"
                    )
                    # Treat as DENY + HALTED (fail-closed)
                    from core.risk_core import RiskState
                    risk_reason = f"Risk Core evaluation returned malformed result → DENY + HALTED"
                    block_level = TraceBlockLevel.HARD if TraceBlockLevel else None
                    trace_entries.append(("RiskCore", False, risk_reason, block_level))
                    logger.error(f"[TRACE] RiskCore → DENY → {risk_reason}")
                    print(f"   🚫 Risk Core evaluation malformed for {symbol}: enforcing DENY + HALTED")
                    self.blocked_signals_count += 1
                    self._update_state()
                    self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                    return  # Early exit - fail-closed enforcement
                
                # Extract result (validated)
                permission, risk_state, violation_report = risk_core_result
                
                # Validate result types (fail-closed)
                from core.risk_core import RiskState
                if not isinstance(permission, TradingPermission) or not isinstance(risk_state, RiskState):
                    logger.critical(
                        f"Risk Core evaluation returned invalid types for {symbol}: "
                        f"permission={type(permission)}, risk_state={type(risk_state)}. "
                        f"ADR-TRADING-RISK-CORE-001 violation: enforcing DENY + HALTED"
                    )
                    # Treat as DENY + HALTED (fail-closed)
                    risk_reason = f"Risk Core evaluation returned invalid types → DENY + HALTED"
                    block_level = TraceBlockLevel.HARD if TraceBlockLevel else None
                    trace_entries.append(("RiskCore", False, risk_reason, block_level))
                    logger.error(f"[TRACE] RiskCore → DENY → {risk_reason}")
                    print(f"   🚫 Risk Core evaluation invalid types for {symbol}: enforcing DENY + HALTED")
                    self.blocked_signals_count += 1
                    self._update_state()
                    self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                    return  # Early exit - fail-closed enforcement
                
                # Логируем решение Risk Core
                risk_allowed = permission != TradingPermission.DENY
                risk_reason = f"Risk state: {risk_state.value}"
                if violation_report and violation_report.violations:
                    risk_reason += f", violations: {len(violation_report.violations)}"
                block_level = TraceBlockLevel.HARD if (not risk_allowed and TraceBlockLevel) else (TraceBlockLevel.NONE if TraceBlockLevel else None)
                trace_entries.append(("RiskCore", risk_allowed, risk_reason, block_level))
                logger.info(f"[TRACE] RiskCore → {'ALLOW' if risk_allowed else 'DENY'} → {risk_reason}")
                
                if permission == TradingPermission.DENY:
                    # Risk Core заблокировал торговлю (veto power)
                    logger.warning(
                        f"Signal blocked by Risk Core for {symbol}: {risk_state.value} "
                        f"(violations: {len(violation_report.violations) if violation_report else 0})"
                    )
                    print(f"   🚫 Risk Core заблокировал сигнал для {symbol}: {risk_state.value}")
                    self.blocked_signals_count += 1
                    self._update_state()
                    # Сохраняем trace ПОСЛЕ принятия решения
                    self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                    return  # Early exit - Risk Core veto
                
                # Если ALLOW_LIMITED, ограничиваем размер позиции
                if permission == TradingPermission.ALLOW_LIMITED:
                    # Ограничиваем размер позиции до 50% от запрошенного
                    original_size = signal_data.get("position_size", 0.0)
                    if original_size > 0:
                        signal_data["position_size"] = original_size * 0.5
                        logger.info(f"Risk Core: Limited position size for {symbol} to 50%")
            
            except Exception as e:
                # FAIL-CLOSED: Any exception during Risk Core evaluation → DENY + HALTED
                # ADR-TRADING-RISK-CORE-001 Section 6: If Risk Core fails → DENY
                logger.critical(
                    f"Risk Core evaluation raised exception for {symbol}: {type(e).__name__}: {e}. "
                    f"ADR-TRADING-RISK-CORE-001 violation: enforcing DENY + HALTED",
                    exc_info=True
                )
                # Treat as DENY + HALTED (fail-closed)
                from core.risk_core import RiskState
                risk_reason = f"Risk Core evaluation exception: {type(e).__name__} → DENY + HALTED"
                block_level = TraceBlockLevel.HARD if TraceBlockLevel else None
                trace_entries.append(("RiskCore", False, risk_reason, block_level))
                logger.error(f"[TRACE] RiskCore → DENY → {risk_reason}")
                print(f"   🚫 Risk Core evaluation exception for {symbol}: enforcing DENY + HALTED")
                self.blocked_signals_count += 1
                self._update_state()
                self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                return  # Early exit - fail-closed enforcement
            
            # ========== META DECISION BRAIN - ОБЯЗАТЕЛЬНЫЙ ФИЛЬТР (ADR-004) ==========
            # Проверяем через MetaDecisionBrain ДО всех остальных проверок
            # ADR-004: fail-closed — если модуль недоступен или падает → BLOCK
            if snapshot:
                meta_result = self._check_meta_decision(snapshot, system_state)
                if meta_result:
                    # Логируем решение MetaDecisionBrain
                    block_level = TraceBlockLevel.HARD if (meta_result.block_level and hasattr(meta_result.block_level, 'value') and meta_result.block_level.value == "HARD") else TraceBlockLevel.NONE
                    trace_entries.append(("META", meta_result.allow_trading, meta_result.reason, block_level))
                    logger.info(f"[TRACE] META → {'ALLOW' if meta_result.allow_trading else 'BLOCK'} → reason={meta_result.reason}")
                    
                    if not meta_result.allow_trading:
                        # MetaDecisionBrain заблокировал торговлю
                        print(f"   🚫 MetaDecisionBrain заблокировал сигнал для {symbol}: {meta_result.reason}")
                        self.blocked_signals_count += 1
                        self._update_state()
                        # Сохраняем trace ПОСЛЕ принятия решения
                        self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                        return  # Early exit - не вызываем DecisionCore, PortfolioBrain
            
            # Проверяем через Gatekeeper
            decision_core_result = self.check_signal(symbol, signal_data, system_state=system_state)
            if not decision_core_result:
                # Логируем решение DecisionCore
                decision = self.decision_core.should_i_trade(symbol=symbol, system_state=system_state)
                trace_entries.append(("DecisionCore", False, decision.reason if decision else "Signal blocked", TraceBlockLevel.NONE))
                logger.info(f"[TRACE] DecisionCore → BLOCK → reason={decision.reason if decision else 'Signal blocked'}")
                print(f"   🚫 Gatekeeper заблокировал сигнал для {symbol}")
                # Сохраняем trace ПОСЛЕ принятия решения
                self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                return
            
            # Логируем решение DecisionCore (если прошло)
            decision = self.decision_core.should_i_trade(symbol=symbol, system_state=system_state)
            trace_entries.append(("DecisionCore", True, decision.reason if decision else "Signal approved", TraceBlockLevel.NONE))
            logger.info(f"[TRACE] DecisionCore → ALLOW → reason={decision.reason if decision else 'Signal approved'}")
            
            # Портфельный анализ (если есть snapshot)
            portfolio_analysis = None
            if snapshot:
                portfolio_analysis = self._check_portfolio(snapshot)
                if portfolio_analysis:
                    # Логируем решение PortfolioBrain
                    portfolio_allowed = portfolio_analysis.decision != PortfolioDecision.BLOCK
                    trace_entries.append(("PortfolioBrain", portfolio_allowed, portfolio_analysis.reason, TraceBlockLevel.NONE))
                    logger.info(f"[TRACE] PortfolioBrain → {'ALLOW' if portfolio_allowed else 'BLOCK'} → reason={portfolio_analysis.reason}")
                    
                    if portfolio_analysis.decision == PortfolioDecision.BLOCK:
                        print(f"   🚫 Portfolio Brain заблокировал сигнал для {symbol}: {portfolio_analysis.reason}")
                        self.blocked_signals_count += 1
                        self._update_state()
                        # Сохраняем trace ПОСЛЕ принятия решения
                        self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                        return
                
                # Применяем размер позиции из portfolio_analysis
                if portfolio_analysis and portfolio_analysis.recommended_size_multiplier < 1.0:
                    original_size = signal_data.get("position_size", 0.0)
                    if original_size > 0:
                        signal_data["position_size"] = original_size * portfolio_analysis.recommended_size_multiplier
                        print(f"   📉 Portfolio Brain уменьшил размер позиции для {symbol}: {portfolio_analysis.reason}")
            
            # ========== POSITION SIZER - ОБЯЗАТЕЛЬНЫЙ ШАГ ПЕРЕД ОТПРАВКОЙ (ADR-004) ==========
            # Рассчитываем финальный размер позиции через PositionSizer
            # ADR-004: fail-closed — если модуль падает → BLOCK
            sizing_result = None  # инициализируем до условного блока
            if snapshot:
                sizing_result = self._calculate_position_size(snapshot, portfolio_analysis)
                if sizing_result:
                    # Логируем решение PositionSizer
                    trace_entries.append(("PositionSizer", sizing_result.position_allowed, sizing_result.reason, TraceBlockLevel.NONE))
                    logger.info(f"[TRACE] PositionSizer → {'ALLOW' if sizing_result.position_allowed else 'BLOCK'} → reason={sizing_result.reason}")
                    
                    if not sizing_result.position_allowed:
                        # PositionSizer заблокировал торговлю (риск слишком мал)
                        logger.info(f"[SIZER] Trade blocked: {sizing_result.reason}")
                        print(f"   🚫 PositionSizer заблокировал сигнал для {symbol}: {sizing_result.reason}")
                        self.blocked_signals_count += 1
                        self._update_state()
                        # Сохраняем trace ПОСЛЕ принятия решения
                        self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="BLOCK")
                        return
                    
                    # Применяем размер позиции из PositionSizer
                    if sizing_result.position_size_usd:
                        # Вычисляем множитель размера
                        original_size = signal_data.get("position_size", 0.0)
                        if original_size > 0:
                            size_multiplier = sizing_result.position_size_usd / original_size
                            signal_data["position_size"] = sizing_result.position_size_usd
                            logger.info(f"[SIZER] size_multiplier={size_multiplier:.2f}, final_risk={sizing_result.final_risk:.2f}%")
                        else:
                            # Если размера не было, используем рассчитанный
                            signal_data["position_size"] = sizing_result.position_size_usd
                            logger.info(f"[SIZER] position_size={sizing_result.position_size_usd:.2f} USDT, final_risk={sizing_result.final_risk:.2f}%")
            
            # Получаем решение для контекста
            decision = self.decision_core.should_i_trade(symbol=symbol, system_state=system_state)
            
            # Формируем сообщение
            try:
                msg = build_signal(
                    symbol, states, risk, directions,
                    zone=signal_data.get("zone"),
                    position_size=signal_data.get("position_size"),
                    leverage=signal_data.get("leverage"),
                    candle_analysis=signal_data.get("candle_analysis")
                )
            except Exception as e:
                logger.error(f"Ошибка формирования сигнала для {symbol}: {type(e).__name__}: {e}", exc_info=True)
                return
            
            # Добавляем контекст от Decision Core
            extra = (
                f"\n\n📊 Score: {score}/125"
                f"\n🧭 Режим: {mode}"
                f"\n📈 R:R: {signal_data.get('rr_ratio', 0):.2f}"
                f"\n⚠️ Risk: {risk}"
                f"\n💹 Volatility: {signal_data.get('volatility_pct', 0):.2f}%"
            )
            
            # Добавляем когнитивные метрики (если есть snapshot)
            if snapshot:
                extra += (
                    f"\n🧠 Confidence: {snapshot.confidence:.2f}"
                    f"\n📊 Entropy: {snapshot.entropy:.2f}"
                )
            
            # Добавляем портфельный анализ (если есть)
            if portfolio_analysis:
                extra += f"\n\n🧺 Portfolio:"
                extra += f"\n• Решение: {portfolio_analysis.decision.value}"
                extra += f"\n• Причина: {portfolio_analysis.reason}"
                if portfolio_analysis.risk_utilization_ratio > 0:
                    extra += f"\n• Экспозиция: {portfolio_analysis.risk_utilization_ratio * 100:.1f}%"
            
            extra += f"\n✅ Decision Core: {decision.reason}"
            
            if decision.recommendations:
                extra += f"\n\n💡 Рекомендации:\n" + "\n".join(f"• {r}" for r in decision.recommendations)
            
            extra += f"\n\nПричины:\n- " + "\n- ".join(reasons)
            
            # Отправляем
            logger.info(f"Отправка сигнала через Gatekeeper для {symbol}...")
            try:
                send_message(msg + extra)
                send_chart(symbol)
                logger.info(f"Сигнал отправлен для {symbol}")
                # Логируем финальное решение - SEND
                logger.info(f"[TRACE] FINAL → SEND → signal sent to user")
                # Сохраняем trace ПОСЛЕ принятия решения
                self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="SEND")
            except Exception as e:
                logger.error(f"Ошибка отправки сигнала для {symbol}: {type(e).__name__}: {e}", exc_info=True)
                # Логируем финальное решение - ERROR
                logger.info(f"[TRACE] FINAL → ERROR → {type(e).__name__}: {e}")
                # Сохраняем trace ПОСЛЕ принятия решения
                self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="ERROR")
                # Не блокируем счетчик, так как проверка прошла успешно

            # ========== EXECUTION (Phase 2) ==========
            # Размещаем ордер если режим TESTNET/LIVE
            self._execute_order(symbol, signal_data, sizing_result)
        except Exception as e:
            # Критическая ошибка
            logger.error(f"Критическая ошибка в Gatekeeper.send_signal для {symbol}: {type(e).__name__}: {e}", exc_info=True)
            # Логируем финальное решение - ERROR
            logger.info(f"[TRACE] FINAL → ERROR → {type(e).__name__}: {e}")
            # Сохраняем trace ПОСЛЕ принятия решения (если есть)
            if 'trace_entries' in locals():
                self._save_decision_trace(symbol, snapshot, trace_entries, final_decision="ERROR")
            self.blocked_signals_count += 1
            self._update_state()
    
    def _execute_order(
        self,
        symbol: str,
        signal_data: Dict,
        sizing_result,
    ) -> None:
        """
        Размещает ордер через OrderExecutor если режим TESTNET или LIVE.
        DRY_RUN / PAPER_TRADING → только логирование, биржа не вызывается.

        Принципы:
        - Fail-closed: любая ошибка логируется и уведомляет в Telegram, но не крашит основной поток
        - Qty рассчитывается из position_size_usd (уже проставлен PositionSizer в signal_data)
        - Market-ордер с SL/TP выставленными на бирже
        """
        mode = get_trading_mode()
        if mode in (TradingMode.DRY_RUN, TradingMode.PAPER_TRADING):
            logger.info("[%s] Order not placed for %s (simulation mode)", mode.value, symbol)
            return

        if mode not in (TradingMode.TESTNET, TradingMode.LIVE):
            logger.warning("Unknown trading mode %s, skipping execution for %s", mode, symbol)
            return

        entry_price = signal_data.get("entry")
        stop_loss = signal_data.get("stop")
        take_profit = signal_data.get("target")
        side = signal_data.get("side")  # "LONG" | "SHORT"

        if not all([entry_price, stop_loss, side]):
            logger.error(
                "[EXECUTOR] Missing required signal fields for %s: entry=%s stop=%s side=%s",
                symbol, entry_price, stop_loss, side,
            )
            return

        position_size_usd = signal_data.get("position_size") or 0.0
        if position_size_usd <= 0:
            logger.error("[EXECUTOR] Invalid position_size_usd=%.2f for %s", position_size_usd, symbol)
            return

        from exchange.bybit_client import get_bybit_client
        from decimal import Decimal, ROUND_DOWN
        try:
            qty_step = get_bybit_client().get_qty_step(symbol)
        except Exception:
            qty_step = 0.001  # conservative fallback
        qty_step_d = Decimal(str(qty_step))
        raw_qty = position_size_usd / entry_price
        qty = float((Decimal(str(raw_qty)) / qty_step_d).to_integral_value(ROUND_DOWN) * qty_step_d)
        if qty <= 0:
            logger.error("[EXECUTOR] Calculated qty=%.4f invalid for %s", qty, symbol)
            return

        from execution.order_executor import get_order_executor, TradeRequest
        from database import save_order, open_position
        from execution.position_tracker import get_position_tracker, TrackedPosition

        executor = get_order_executor()
        client_order_id = f"mbot_{symbol}_{int(datetime.now(UTC).timestamp())}"
        request = TradeRequest(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=None,  # Market ордер
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_order_id,
        )

        try:
            result = executor.execute(request)
        except Exception as e:
            logger.error(
                "[EXECUTOR] Unexpected error placing order for %s: %s: %s",
                symbol, type(e).__name__, e, exc_info=True,
            )
            send_message(
                f"❌ Ошибка размещения ордера {symbol} {side}: внутренняя ошибка исполнителя."
            )
            return

        if result.success:
            order_id = result.order_id
            logger.info(
                "[EXECUTOR] Order placed: %s %s qty=%.4f order_id=%s dry_run=%s",
                symbol, side, qty, order_id, result.dry_run,
            )
            try:
                save_order(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    order_type="Market",
                    qty=qty,
                    stop_loss=stop_loss,
                    dry_run=result.dry_run,
                    entry_price=entry_price,
                    take_profit=take_profit,
                    status="FILLED",
                )
                open_position(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                )
                tracker = get_position_tracker()
                tracker.add(TrackedPosition(
                    symbol=symbol,
                    side=side,
                    entry_price=entry_price,
                    qty=qty,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    order_id=order_id,
                ))
            except Exception as e:
                logger.error(
                    "[EXECUTOR] DB/tracker error after order %s: %s: %s",
                    order_id, type(e).__name__, e, exc_info=True,
                )

            tp_str = f"{take_profit:.4f}" if take_profit else "N/A"
            send_message(
                f"✅ Ордер размещён [{mode.value}]\n"
                f"📈 {symbol} {side}\n"
                f"💰 Qty: {qty} | Size: ${position_size_usd:.1f}\n"
                f"🎯 Entry: {entry_price:.4f}\n"
                f"🛡 SL: {stop_loss:.4f} | TP: {tp_str}\n"
                f"🆔 {order_id}"
            )
        else:
            logger.error("[EXECUTOR] Order failed for %s: %s", symbol, result.error)
            send_message(
                f"❌ Ордер не исполнен: {symbol} {side}\n"
                f"Причина: {result.error or 'unknown'}"
            )

    def _check_portfolio(self, snapshot: SignalSnapshot) -> Optional[PortfolioAnalysis]:
        """
        Проверяет сигнал через Portfolio Brain.
        
        Args:
            snapshot: SignalSnapshot для анализа
        
        Returns:
            PortfolioAnalysis или None (если нет открытых позиций)
        """
        try:
            # Получаем открытые сделки
            open_trades = get_open_trades()
            if not open_trades:
                return None  # Нет позиций - портфельный анализ не нужен
            
            # Преобразуем в PositionSnapshot
            open_positions = convert_trades_to_positions(open_trades)
            
            # Вычисляем PortfolioState
            current_balance = get_current_balance()
            risk_budget = current_balance * (RISK_PERCENT / 100.0) * len(open_trades)  # Упрощённо
            
            portfolio_state = calculate_portfolio_state(
                open_positions=open_positions,
                risk_budget=risk_budget,
                initial_balance=INITIAL_BALANCE
            )
            
            # Анализируем через Portfolio Brain
            analysis = self.portfolio_brain.evaluate(
                snapshot=snapshot,
                open_positions=open_positions,
                portfolio_state=portfolio_state
            )
            
            return analysis
        except Exception as e:
            logger.error(f"Ошибка портфельного анализа для {snapshot.symbol}: {type(e).__name__}: {e}", exc_info=True)
            return None  # В случае ошибки не блокируем сигнал
    
    def _check_signal_quality(self, signal_data: Dict, decision: TradingDecision) -> bool:
        """
        Проверяет качество сигнала.
        
        Args:
            signal_data: Данные сигнала
            decision: Решение Decision Core
            
        Returns:
            bool: True если качество приемлемо
        """
        # Проверяем размер позиции
        position_size = signal_data.get("position_size")
        if decision.max_position_size and position_size:
            if position_size > decision.max_position_size:
                return False
        
        # Проверяем плечо
        leverage = signal_data.get("leverage")
        if decision.max_leverage and leverage:
            if leverage > decision.max_leverage:
                return False
        
        return True
    
    def _update_state(self):
        """Обновляет явное состояние"""
        self.state = {
            "blocked": self.blocked_signals_count,
            "approved": self.approved_signals_count,
            "total": self.blocked_signals_count + self.approved_signals_count
        }
    
    def _log_blocked_signal(self, symbol: str, decision: TradingDecision):
        """Логирует заблокированный сигнал"""
        print(f"🚫 Gatekeeper заблокировал сигнал для {symbol}: {decision.reason}")
    
    def _check_meta_decision(
        self, 
        snapshot: SignalSnapshot, 
        system_state
    ) -> Optional[MetaDecisionResult]:
        """
        Проверяет сигнал через MetaDecisionBrain.
        
        Args:
            snapshot: SignalSnapshot для анализа
            system_state: Состояние системы
            
        Returns:
            MetaDecisionResult или None (если MetaDecisionBrain недоступен)
        """
        try:
            # Извлекаем данные из snapshot и system_state
            market_regime = snapshot.market_regime
            confidence_score = snapshot.confidence
            entropy_score = snapshot.entropy
            
            # Вычисляем portfolio_exposure из открытых позиций
            portfolio_exposure = 0.0
            try:
                open_trades = get_open_trades()
                if open_trades:
                    current_balance = get_current_balance()
                    if current_balance > 0:
                        # Упрощённый расчёт: сумма всех позиций / баланс
                        total_exposure = sum(trade.get("size", 0) for trade in open_trades)
                        portfolio_exposure = min(1.0, total_exposure / current_balance)
            except Exception:
                portfolio_exposure = 0.0
            
            # Получаем signals_count_recent из system_state
            signals_count_recent = len(system_state.recent_signals) if system_state and hasattr(system_state, 'recent_signals') else 0
            
            # Преобразуем system_health в SystemHealthStatus
            system_health = SystemHealthStatus.OK
            if system_state and hasattr(system_state, 'system_health'):
                if system_state.system_health.safe_mode or system_state.system_health.consecutive_errors > 5:
                    system_health = SystemHealthStatus.DEGRADED
            
            # Вызываем MetaDecisionBrain
            meta_result = self.meta_decision_brain.evaluate(
                market_regime=market_regime,
                confidence_score=confidence_score,
                entropy_score=entropy_score,
                portfolio_exposure=portfolio_exposure,
                recent_outcomes=None,  # Опционально - можно добавить позже
                signals_count_recent=signals_count_recent,
                system_health=system_health,
                time_context=TimeContext.UNKNOWN  # Опционально - можно улучшить позже
            )
            
            return meta_result
        except Exception as e:
            # ADR-004: fail-closed — runtime exception → HARD_BLOCK
            logger.critical(
                f"MetaDecisionBrain exception for {snapshot.symbol}: {type(e).__name__}: {e}. "
                f"ADR-004: fail-closed → HARD_BLOCK",
                exc_info=True
            )
            get_system_guardian().report_module_failure_sync(
                "MetaDecisionBrain", "runtime_error",
                {"error": type(e).__name__, "message": str(e), "symbol": snapshot.symbol}
            )
            return MetaDecisionResult(
                allow_trading=False,
                reason=f"MetaDecisionBrain exception: {type(e).__name__} → fail-closed HARD_BLOCK",
                block_level=MetaBlockLevel.HARD,
                cooldown_minutes=MetaDecisionBrain.HARD_BLOCK_COOLDOWN_MINUTES
            )
    
    def _calculate_position_size(
        self,
        snapshot: SignalSnapshot,
        portfolio_analysis: Optional[PortfolioAnalysis]
    ):
        """
        Рассчитывает размер позиции через PositionSizer.
        
        Args:
            snapshot: SignalSnapshot для анализа
            portfolio_analysis: Результат PortfolioBrain (опционально)
        
        Returns:
            PositionSizingResult или None (если PositionSizer недоступен)
        
        Примечание:
            Вызывается ПОСЛЕ всех проверок, но ДО отправки сигнала.
        """
        try:
            # Вычисляем portfolio_state (используем ту же логику, что и в _check_portfolio)
            open_trades = get_open_trades()
            portfolio_state = None
            
            if open_trades:
                open_positions = convert_trades_to_positions(open_trades)
                current_balance = get_current_balance()
                risk_budget = current_balance * (RISK_PERCENT / 100.0) * len(open_trades)
                portfolio_state = calculate_portfolio_state(
                    open_positions=open_positions,
                    risk_budget=risk_budget,
                    initial_balance=INITIAL_BALANCE
                )
            else:
                # Пустой портфель - создаём минимальный PortfolioState
                from core.portfolio_brain import PortfolioState
                portfolio_state = PortfolioState(
                    total_exposure=0.0,
                    long_exposure=0.0,
                    short_exposure=0.0,
                    net_exposure=0.0,
                    risk_budget=get_current_balance() * (RISK_PERCENT / 100.0),
                    used_risk=0.0
                )
            
            # Используем PortfolioStateAdapter для совместимости с PositionSizer
            portfolio_adapter = PortfolioStateAdapter(portfolio_state)
            
            # Получаем баланс
            balance = get_current_balance()
            
            # Вызываем PositionSizer
            sizing_result = self.position_sizer.calculate(
                confidence=snapshot.confidence,
                entropy=snapshot.entropy,
                portfolio_state=portfolio_adapter,
                symbol=snapshot.symbol,
                balance=balance
            )
            
            return sizing_result
        except Exception as e:
            # ADR-004: fail-closed — runtime exception → position blocked
            logger.critical(
                f"PositionSizer exception for {snapshot.symbol}: {type(e).__name__}: {e}. "
                f"ADR-004: fail-closed → position blocked",
                exc_info=True
            )
            get_system_guardian().report_module_failure_sync(
                "PositionSizer", "runtime_error",
                {"error": type(e).__name__, "message": str(e), "symbol": snapshot.symbol}
            )
            from core.position_sizer import PositionSizingResult
            return PositionSizingResult(
                position_allowed=False,
                final_risk=0.0,
                base_risk=0.0,
                confidence_factor=0.0,
                entropy_factor=0.0,
                portfolio_factor=0.0,
                reason=f"PositionSizer exception: {type(e).__name__} → fail-closed block"
            )
    
    def _save_decision_trace(
        self,
        symbol: str,
        snapshot: Optional[SignalSnapshot],
        trace_entries: List[tuple],
        final_decision: str
    ):
        """
        Сохраняет trace решений в DecisionTrace ПОСЛЕ принятия решения.
        
        Args:
            symbol: Торговая пара
            snapshot: SignalSnapshot (опционально)
            trace_entries: Список решений [(source, allow_trading, reason, block_level), ...]
            final_decision: Финальное решение (SEND, BLOCK, ERROR)
        
        Примечание:
            Вызывается ПОСЛЕ принятия решения, не влияет на runtime-логику.
        """
        if not self.trace_enabled or not self.decision_trace:
            return
        
        try:
            # Сохраняем каждое решение из trace
            for source, allow_trading, reason, block_level in trace_entries:
                # Формируем context_snapshot из snapshot (если есть)
                context_snapshot = {}
                if snapshot:
                    context_snapshot = {
                        "confidence": snapshot.confidence,
                        "entropy": snapshot.entropy,
                        "score": snapshot.score,
                        "risk_level": snapshot.risk_level.value if snapshot.risk_level else None,
                        "market_regime": snapshot.market_regime.trend_type if snapshot.market_regime else None
                    }
                
                # Добавляем финальное решение в контекст
                context_snapshot["final_decision"] = final_decision
                
                # Сохраняем в DecisionTrace
                self.decision_trace.log_decision(
                    symbol=symbol,
                    decision_source=source,
                    allow_trading=allow_trading,
                    block_level=block_level,
                    reason=reason,
                    context_snapshot=context_snapshot
                )
            
            # Сохраняем финальное решение
            final_allow = final_decision == "SEND"
            self.decision_trace.log_decision(
                symbol=symbol,
                decision_source="Gatekeeper",
                allow_trading=final_allow,
                block_level=TraceBlockLevel.NONE if final_allow else TraceBlockLevel.HARD,
                reason=f"Final decision: {final_decision}",
                context_snapshot={"final_decision": final_decision, "trace_entries_count": len(trace_entries)}
            )
        except Exception as e:
            # Не выбрасываем исключение - trace не должен влиять на торговую логику
            logger.warning(f"Ошибка сохранения DecisionTrace для {symbol}: {type(e).__name__}: {e}")
    
    def _check_risk_core(
        self,
        symbol: str,
        signal_data: Dict,
        system_state
    ) -> Optional[tuple]:
        """
        Проверяет сигнал через Risk Core.
        
        ADR-TRADING-RISK-CORE-001: Risk Core имеет право вето
        
        Args:
            symbol: Торговая пара
            signal_data: Данные сигнала
            system_state: Состояние системы
        
        Returns:
            Tuple (TradingPermission, RiskState, ViolationReport) или None (если ошибка)
        """
        try:
            # Собираем Trading Intent (ADR-TRADING-RISK-CORE-001 Section 4: Inputs ONLY)
            zone = signal_data.get("zone", {})
            entry_price = zone.get("entry", 0.0)
            stop_price = zone.get("stop", 0.0)
            position_size_usd = signal_data.get("position_size", 0.0)
            leverage = signal_data.get("leverage")
            side = "LONG" if signal_data.get("side") == "LONG" else "SHORT"
            
            if entry_price <= 0 or stop_price <= 0 or position_size_usd <= 0:
                # Недостаточно данных для Risk Core - fail-closed
                logger.warning(f"Risk Core: Insufficient signal data for {symbol}")
                return None
            
            intent = TradingIntent(
                symbol=symbol,
                side=side,
                position_size_usd=position_size_usd,
                entry_price=entry_price,
                stop_price=stop_price,
                leverage=leverage
            )
            
            # Собираем Capital Snapshot
            current_balance = get_current_balance()
            
            # Получаем статистику для расчета потерь
            stats_24h = get_trade_statistics(days=1) or {}
            stats_7d = get_trade_statistics(days=7) or {}
            
            total_loss_usd = max(0, INITIAL_BALANCE - current_balance)
            loss_24h_usd = abs(stats_24h.get("total_pnl", 0.0)) if stats_24h.get("total_pnl", 0) < 0 else 0.0
            loss_7d_usd = abs(stats_7d.get("total_pnl", 0.0)) if stats_7d.get("total_pnl", 0) < 0 else 0.0
            
            capital = CapitalSnapshot(
                current_balance_usd=current_balance,
                initial_balance_usd=INITIAL_BALANCE,
                total_loss_usd=total_loss_usd,
                loss_24h_usd=loss_24h_usd,
                loss_7d_usd=loss_7d_usd
            )
            
            # Собираем Exposure Snapshot
            open_trades = get_open_trades()
            open_positions = [
                PositionSnapshot(
                    symbol=trade.get("symbol", ""),
                    side=trade.get("side", "LONG"),
                    position_size_usd=float(trade.get("position_size", 0)),
                    entry_price=float(trade.get("entry", 0)),
                    stop_price=float(trade.get("stop", 0)),
                    leverage=trade.get("leverage")
                )
                for trade in open_trades
            ]
            
            total_exposure_usd = sum(pos.position_size_usd for pos in open_positions)
            max_single_position_usd = max([pos.position_size_usd for pos in open_positions], default=0.0)
            
            # Correlation groups (strategy-blind) - упрощенная реализация
            # В реальной системе это должно быть вычислено из корреляций
            correlation_groups = {}  # Пусто по умолчанию
            
            exposure = ExposureSnapshot(
                open_positions=open_positions,
                total_exposure_usd=total_exposure_usd,
                max_single_position_usd=max_single_position_usd,
                correlation_groups=correlation_groups
            )
            
            # Собираем Behavioral Counters
            # Упрощенная реализация - в реальной системе это должно отслеживаться
            recent_signals = getattr(system_state, 'recent_signals', []) if system_state else []
            actions_last_hour = len([s for s in recent_signals if (datetime.now(UTC) - s.get('timestamp', datetime.now(UTC))).total_seconds() < 3600])
            actions_last_24h = len([s for s in recent_signals if (datetime.now(UTC) - s.get('timestamp', datetime.now(UTC))).total_seconds() < 86400])
            
            # Получаем информацию о потерях из статистики
            consecutive_losses = 0
            last_loss_timestamp = None
            if stats_24h:
                losing_trades = stats_24h.get("losing_trades", 0)
                if losing_trades > 0:
                    consecutive_losses = losing_trades
                    # Приблизительная временная метка последней потери
                    last_loss_timestamp = datetime.now(UTC) - timedelta(hours=1)
            
            behavioral = BehavioralCounters(
                actions_last_hour=actions_last_hour,
                actions_last_24h=actions_last_24h,
                consecutive_losses=consecutive_losses,
                last_loss_timestamp=last_loss_timestamp,
                last_action_timestamp=recent_signals[-1].get('timestamp') if recent_signals else None
            )
            
            # Собираем System Health Flags
            system_health = SystemHealthFlags(
                is_safe_mode=getattr(system_state, 'system_health', None).safe_mode if (system_state and hasattr(system_state, 'system_health')) else False,
                consecutive_errors=getattr(system_state, 'system_health', None).consecutive_errors if (system_state and hasattr(system_state, 'system_health')) else 0,
                runtime_healthy=not (getattr(system_state, 'system_health', None).safe_mode if (system_state and hasattr(system_state, 'system_health')) else False),
                critical_modules_available=True  # Упрощенно - в реальной системе проверять через SystemGuardian
            )
            
            # Вызываем Risk Core
            permission, risk_state, violation_report = self.risk_core.evaluate(
                intent=intent,
                capital=capital,
                exposure=exposure,
                behavioral=behavioral,
                system_health=system_health
            )
            
            return (permission, risk_state, violation_report)
            
        except Exception as e:
            # FAIL-CLOSED: Any exception during Risk Core evaluation → propagate to caller
            # Caller will enforce DENY + HALTED (ADR-TRADING-RISK-CORE-001 Section 6)
            # Do NOT return None here - let exception propagate so caller can enforce fail-closed
            logger.error(
                f"Risk Core evaluation raised exception for {symbol}: {type(e).__name__}: {e}. "
                f"Exception will propagate to caller for fail-closed enforcement.",
                exc_info=True
            )
            raise  # Propagate exception - caller enforces DENY + HALTED
    
    def get_stats(self) -> Dict:
        """Получить статистику Gatekeeper"""
        # Используем явное состояние
        self._update_state()
        return self.state.copy()


# Глобальный экземпляр
_gatekeeper = None

def get_gatekeeper() -> Gatekeeper:
    """Получить глобальный экземпляр Gatekeeper"""
    global _gatekeeper
    if _gatekeeper is None:
        _gatekeeper = Gatekeeper()
    return _gatekeeper

