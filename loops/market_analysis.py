"""
Цикл анализа рынка: оборот (run_market_analysis) — свечи, мозги экосистемы,
Decision Core, сигналы, снимки — и сам цикл (market_analysis_loop): расписание,
адаптивный интервал, восстановление из SAFE_MODE, метрики, опрос позиций.

Вынесен из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 8б; поведение закреплено тестами шага 8а —
tests/test_analysis_cycle.py). Тело не менялось: всё, что принадлежит runner, —
настройки, его функции (get_shutdown_event, evaluate_and_send_alerts,
exit_safe_mode_via_recovery), system_state и RUNNING_TASKS — runner передаёт
один раз через configure() под прежними именами. Единственная правка — список
символов: _active_symbols переприсваивается в main(), поэтому приходит
функцией get_active_symbols.
"""
import asyncio
import logging
import time
import traceback
from brains.cognitive_filter import get_cognitive_filter
from brains.market_regime_brain import get_market_regime_brain
from brains.opportunity_awareness import get_opportunity_awareness
from brains.risk_exposure_brain import get_risk_exposure_brain
from config import SYMBOLS, TIMEFRAMES
from core.decision_core import get_decision_core
from correlation_analysis import analyze_market_correlations
from data_loader import get_candles_parallel
from error_alert import error_alert
from execution.gatekeeper import get_gatekeeper
from signal_generator import generate_signals_for_symbols
from spike_alert import check_all_symbols_for_spikes
from system_state_machine import get_state_machine, SystemState as SystemStateEnum
from telegram_bot import send_message_async
from time_filter import is_good_time
from utils import liveness

from control_plane import state as cp_state
from control_plane.state import get_adaptive_system_state, increment_analysis_cycles, record_analysis_duration, update_analysis_metrics, update_volatility_state

logger = logging.getLogger(__name__)

# Общее состояние процесса — те же объекты, что у runner и HTTP-панели
_adaptive_system_state = cp_state.adaptive_system_state
_analysis_metrics = cp_state.analysis_metrics
_control_plane_state = cp_state.control_plane_state

# ---------------------------------------------------------------------------
# Передаёт runner через configure() — под прежними именами, тело цикла их читает
# ---------------------------------------------------------------------------
_INJECTED = ('ADAPTIVE_INTERVAL_ENABLED', 'ADAPTIVE_INTERVAL_MAX', 'ADAPTIVE_INTERVAL_MIN', 'ADAPTIVE_INTERVAL_MULTIPLIER', 'ADAPTIVE_STABLE_CYCLES', 'ANALYSIS_INTERVAL', 'AUTO_RESUME_SAFE_MODE_DELAY', 'AUTO_RESUME_SUCCESS_CYCLES', 'AUTO_RESUME_TRADING_ENABLED', 'ERROR_PAUSE', 'ITERATION_BUDGET_SECONDS', 'MAX_ANALYSIS_TIME', 'MAX_CONSECUTIVE_ERRORS', 'METRICS_LOG_INTERVAL', 'RUNNING_TASKS', 'SAFE_MODE_RECOVERY_INTERVAL', 'evaluate_and_send_alerts', 'exit_safe_mode_via_recovery', 'get_shutdown_event', 'system_state', 'get_active_symbols')

ADAPTIVE_INTERVAL_ENABLED = None
ADAPTIVE_INTERVAL_MAX = None
ADAPTIVE_INTERVAL_MIN = None
ADAPTIVE_INTERVAL_MULTIPLIER = None
ADAPTIVE_STABLE_CYCLES = None
ANALYSIS_INTERVAL = None
AUTO_RESUME_SAFE_MODE_DELAY = None
AUTO_RESUME_SUCCESS_CYCLES = None
AUTO_RESUME_TRADING_ENABLED = None
ERROR_PAUSE = None
ITERATION_BUDGET_SECONDS = None
MAX_ANALYSIS_TIME = None
MAX_CONSECUTIVE_ERRORS = None
METRICS_LOG_INTERVAL = None
RUNNING_TASKS = None
SAFE_MODE_RECOVERY_INTERVAL = None
evaluate_and_send_alerts = None
exit_safe_mode_via_recovery = None
get_shutdown_event = None
system_state = None
get_active_symbols = None


def configure(**values):
    """Настройки и объекты runner; все имена из _INJECTED обязательны, лишние запрещены."""
    missing = sorted(set(_INJECTED) - set(values))
    unknown = sorted(set(values) - set(_INJECTED))
    if missing or unknown:
        raise TypeError(f"market_analysis.configure(): не хватает {missing}, лишние {unknown}")
    globals().update(values)


# ========== ITERATION BUDGET ENFORCEMENT ==========
async def cooperative_yield():
    """
    Cooperative yield point - yields control back to event loop.
    
    This ensures the event loop remains responsive during long-running iterations.
    Should be called periodically within iteration loops.
    
    CRITICAL: Also checks shutdown state - if shutdown initiated, raises CancelledError
    to allow graceful shutdown to proceed.
    """
    # Check shutdown state before yielding
    shutdown_evt = get_shutdown_event()
    if shutdown_evt.is_set():
        # Shutdown initiated - raise CancelledError to stop iteration slicing
        raise asyncio.CancelledError("Shutdown initiated during iteration")
    
    # Yield control to event loop
    await asyncio.sleep(0)


class IterationBudgetTracker:
    """
    Tracks wall-time budget for a single iteration.
    
    Enforces soft time limits to prevent iteration-level stalls.
    """
    def __init__(self, budget_seconds: float):
        self.budget_seconds = budget_seconds
        self.start_time = None
        self.last_yield_time = None
        self.yield_interval = 5.0  # Yield every 5 seconds during iteration
    
    def start(self):
        """Start tracking iteration budget"""
        self.start_time = time.monotonic()
        self.last_yield_time = self.start_time
    
    def elapsed(self) -> float:
        """Get elapsed time since start"""
        if self.start_time is None:
            return 0.0
        return time.monotonic() - self.start_time
    
    def remaining(self) -> float:
        """Get remaining budget"""
        return max(0.0, self.budget_seconds - self.elapsed())
    
    def is_exceeded(self) -> bool:
        """Check if budget is exceeded"""
        return self.elapsed() > self.budget_seconds
    
    async def check_and_yield(self, force_yield: bool = False) -> bool:
        """
        Check if we should yield and yield if needed.
        
        Args:
            force_yield: If True, always yield regardless of interval
        
        Returns:
            True if budget is still available and iteration should continue,
            False if budget exceeded (iteration should defer remaining work)
        
        Raises:
            asyncio.CancelledError: If shutdown initiated
        """
        # CRITICAL: Check shutdown state first
        shutdown_evt = get_shutdown_event()
        if shutdown_evt.is_set():
            # Shutdown initiated - raise CancelledError to stop iteration slicing
            raise asyncio.CancelledError("Shutdown initiated during iteration")
        
        now = time.monotonic()
        
        # Yield periodically to keep event loop responsive
        # Also yield if forced (e.g., after each symbol in nested loop)
        if force_yield or (now - self.last_yield_time >= self.yield_interval):
            await cooperative_yield()
            self.last_yield_time = now
        
        # Check if budget exceeded
        if self.is_exceeded():
            return False
        
        return True


async def run_market_analysis():
    """
    Выполняет один цикл анализа рынка.
    Это async версия того, что делал main.py
    
    ITERATION BUDGET ENFORCEMENT:
    - Tracks wall-time budget per iteration (ITERATION_BUDGET_SECONDS = 60s)
    - Yields control to event loop periodically (every 5s)
    - Checks shutdown state at each yield point
    - If budget exceeded, defers remaining work to next iteration (returns False)
    - Prevents single iteration from blocking event loop beyond watchdog threshold (300s)
    - Shutdown-aware: raises CancelledError if shutdown initiated during iteration
    """
    import time
    
    # Initialize iteration budget tracker
    # CRITICAL: Use aggressive budget (60s) to prevent LOOP_GUARD_TIMEOUT (300s)
    # This ensures watchdog heartbeat can always observe progress
    budget_tracker = IterationBudgetTracker(ITERATION_BUDGET_SECONDS)
    budget_tracker.start()
    
    # Record iteration start time (as required)
    iteration_start = time.monotonic()
    
    start_time = time.time()
    symbols = get_active_symbols() or SYMBOLS
    logger.info("🚀 Начало анализа %d символов", len(symbols))
    
    # Проверка торгового времени
    if not is_good_time():
        logger.info("⏸ Не торговое время - пропускаем цикл")
        return True
    
    try:
        # Cooperative yield after initial checks
        await cooperative_yield()
        
        # Инициализация экосистемы
        logger.info("🧠 Инициализация экосистемы...")
        decision_core = get_decision_core()
        market_regime_brain = get_market_regime_brain()
        risk_exposure_brain = get_risk_exposure_brain()
        cognitive_filter = get_cognitive_filter()
        opportunity_awareness = get_opportunity_awareness()
        gatekeeper = get_gatekeeper()
        
        # Check budget and yield
        if not await budget_tracker.check_and_yield():
            logger.warning("⏱ Iteration budget exceeded (%.1fs) after initialization - continuing with degraded mode", budget_tracker.elapsed())
        
        # Параллельная загрузка данных (синхронная операция в отдельном потоке)
        logger.info("📥 Параллельная загрузка данных...")
        load_start = time.time()
        # Используем asyncio.to_thread для синхронных операций с timeout
        try:
            all_candles = await asyncio.wait_for(
                asyncio.to_thread(get_candles_parallel, symbols, TIMEFRAMES, 120, 20),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            # TimeoutError при загрузке данных - мягкое предупреждение, не авария
            load_duration = time.time() - load_start
            logger.warning(
                "⏱ Data loading slow: %.2fs (timeout=60s). Continuing with degraded mode.",
                load_duration
            )
            # Не активируем safe_mode и не возвращаем False - продолжаем работу
            # Записываем для метрик, но не блокируем анализ
            system_state.record_error("Data loading timeout (non-critical)")
            return False  # Возвращаем False, но не активируем safe_mode
        load_time = time.time() - load_start
        logger.info("✅ Данные загружены за %.2f секунд", load_time)
        
        # Check budget and yield after data loading (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning("⏱ Iteration budget exceeded (%.1fs) after data loading - deferring remaining work to next iteration", budget_tracker.elapsed())
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Анализ "мозгами" экосистемы (синхронные операции в потоках)
        # Brain'ы обновляют SystemState напрямую, не через DecisionCore
        logger.debug("🧠 Анализ Market Regime Brain...")
        try:
            market_regime = await asyncio.wait_for(
                asyncio.to_thread(market_regime_brain.analyze, symbols, all_candles, system_state),
                timeout=30.0
            )
            logger.info("   Режим: %s, Волатильность: %s, Risk: %s", market_regime.trend_type, market_regime.volatility_level, market_regime.risk_sentiment)
            # Обновляем состояние волатильности для адаптивной системы
            if market_regime and hasattr(market_regime, 'volatility_level'):
                update_volatility_state(market_regime.volatility_level)
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Market Regime Brain (30 сек)")
            market_regime = None
        except Exception as e:
            logger.error("⚠️ Ошибка в Market Regime Brain: %s: %s", type(e).__name__, e)
            market_regime = None
        
        logger.debug("🧠 Анализ Risk & Exposure Brain...")
        try:
            risk_exposure = await asyncio.wait_for(
                asyncio.to_thread(risk_exposure_brain.analyze, symbols, all_candles, system_state),
                timeout=30.0
            )
            logger.info("   Риск: %.2f%%, Позиций: %s, Перегрузка: %s", risk_exposure.total_risk_pct, risk_exposure.active_positions, risk_exposure.is_overloaded)
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Risk & Exposure Brain (30 сек)")
            risk_exposure = None
        except Exception as e:
            logger.error("⚠️ Ошибка в Risk & Exposure Brain: %s: %s", type(e).__name__, e)
            risk_exposure = None
        
        logger.debug("🧠 Анализ Cognitive Filter...")
        try:
            cognitive_state = await asyncio.wait_for(
                asyncio.to_thread(cognitive_filter.analyze, system_state),
                timeout=30.0
            )
            logger.debug("   Пере-торговля: %.2f, Пауза: %s", cognitive_state.overtrading_score, cognitive_state.should_pause)
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Cognitive Filter (30 сек)")
            cognitive_state = None
        except Exception as e:
            logger.error("⚠️ Ошибка в Cognitive Filter: %s: %s", type(e).__name__, e)
            cognitive_state = None
        
        # Check budget and yield after brain analysis (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning("⏱ Iteration budget exceeded (%.1fs) after brain analysis - deferring remaining work to next iteration", budget_tracker.elapsed())
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Проверка через Decision Core (читает из SystemState)
        try:
            # В пуле потоков: should_i_trade читает базу (а в LIVE — кошелёк по сети).
            global_decision = await asyncio.to_thread(decision_core.should_i_trade, system_state=system_state)
        except RuntimeError as e:
            # Обработка fault injection или других RuntimeError из DecisionCore
            if "FAULT_INJECTION: decision_exception" in str(e):
                # Fault injection - логируем структурированно и продолжаем
                logger.error(
                    "FAULT_INJECTION: decision_exception - Controlled exception from DecisionCore.should_i_trade(). Runtime continues. error_type=RuntimeError error_message=%s",
                    e
                )
                # Записываем ошибку для health tracking
                system_state.record_error("FAULT_INJECTION: decision_exception")
                
                # HARDENING: Проверяем safe-mode активацию через state machine
                state_machine = get_state_machine()
                if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    if not state_machine.is_safe_mode:
                        await state_machine.transition_to(
                            SystemStateEnum.SAFE_MODE,
                            reason=f"Fault injection: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                            owner="error_alert",
                            metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                        )
                        logger.warning(
                            "SAFE-MODE activated after fault injection: consecutive_errors=%s >= MAX_CONSECUTIVE_ERRORS=%s",
                            system_state.system_health.consecutive_errors, MAX_CONSECUTIVE_ERRORS
                        )
                
                # Возвращаем False для цикла анализа (ошибка обработана)
                return False
            else:
                # Другие RuntimeError - пробрасываем дальше
                raise
        
        if not global_decision.can_trade:
            logger.info("⏸ Decision Core блокирует торговлю: %s", global_decision.reason)
            _notify_task = asyncio.create_task(
                send_message_async(f"🧠 Decision Core: {global_decision.reason}\n\nРекомендации:\n" + "\n".join(f"• {r}" for r in global_decision.recommendations)),
                name="TelegramNotify",
            )
            RUNNING_TASKS.add(_notify_task)
            _notify_task.add_done_callback(RUNNING_TASKS.discard)
            # Анализ отработал (данные, мозги, решение): вето — решение не торговать, а не
            # сбой. Без сброса в SAFE_MODE, где Decision Core всегда накладывает вето,
            # ошибки не обнулялись и восстановление не начиналось (11.09.2026).
            system_state.reset_errors()
            return True
        
        # Check budget and yield after decision core check (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning("⏱ Iteration budget exceeded (%.1fs) after decision core - deferring remaining work to next iteration", budget_tracker.elapsed())
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Проверка резких движений
        logger.info("🔍 Проверка резких движений...")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(check_all_symbols_for_spikes, symbols, all_candles),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.warning("⏱ Таймаут проверки резких движений")
        except Exception as e:
            logger.warning("⚠️ Ошибка при проверке резких движений: %s", e)
        
        # Check budget and yield after spike check (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning("⏱ Iteration budget exceeded (%.1fs) after spike check - deferring remaining work to next iteration", budget_tracker.elapsed())
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Анализ корреляций
        logger.info("📊 Анализ корреляций между парами...")
        try:
            market_correlations = await asyncio.wait_for(
                asyncio.to_thread(analyze_market_correlations, symbols, all_candles, "15m"),
                timeout=30.0
            )
            # Обновляем SystemState с корреляциями
            system_state.update_market_correlations(market_correlations)
        except asyncio.TimeoutError:
            logger.warning("⏱ Таймаут анализа корреляций")
            market_correlations = {}
        except Exception as e:
            logger.warning("⚠️ Ошибка при анализе корреляций: %s", e)
            market_correlations = {}
        
        # Check budget and yield after correlation analysis (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning("⏱ Iteration budget exceeded (%.1fs) after correlation analysis - deferring remaining work to next iteration", budget_tracker.elapsed())
                return False  # Defer remaining work to next iteration
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Генерация сигналов
        logger.info("📊 Генерация сигналов...")
        try:
            signal_stats = await asyncio.wait_for(
                asyncio.to_thread(
                    generate_signals_for_symbols,
                    all_candles=all_candles,
                    market_correlations=market_correlations,
                    good_time=True,
                    decision_core=decision_core,
                    opportunity_awareness=opportunity_awareness,
                    gatekeeper=gatekeeper,
                    system_state=system_state
                ),
                timeout=120.0
            )
            logger.info(
                "📊 Статистика сигналов: обработано %s, отправлено %s, заблокировано %s, ошибок %s",
                signal_stats['processed'], signal_stats['signals_sent'], signal_stats['signals_blocked'], signal_stats['errors']
            )
        except asyncio.TimeoutError:
            # TimeoutError при генерации сигналов - мягкое предупреждение, не авария
            logger.warning(
                "⏱ Signal generation slow: exceeded timeout=120s. Continuing with degraded mode."
            )
            # Не активируем safe_mode - продолжаем работу
            # Записываем для метрик, но не блокируем анализ
            system_state.record_error("Signal generation timeout (non-critical)")
            # Продолжаем выполнение - не возвращаем False, чтобы цикл продолжался
        except Exception as e:
            logger.error("⚠️ Ошибка при генерации сигналов: %s: %s", type(e).__name__, e)
            logger.error(traceback.format_exc())
        
        # Check budget and yield after signal generation (shutdown-aware)
        try:
            if not await budget_tracker.check_and_yield():
                logger.warning("⏱ Iteration budget exceeded (%.1fs) after signal generation - deferring remaining work to next iteration", budget_tracker.elapsed())
                # Note: Signal generation is the last major step, so we continue to completion
        except asyncio.CancelledError:
            logger.info("Iteration cancelled due to shutdown")
            raise
        
        # Статистика Gatekeeper
        gatekeeper_stats = gatekeeper.get_stats()
        if gatekeeper_stats["total"] > 0:
            logger.info("🚪 Gatekeeper: одобрено %s, заблокировано %s", gatekeeper_stats['approved'], gatekeeper_stats['blocked'])
        
        total_time = time.time() - start_time
        elapsed_budget = budget_tracker.elapsed()
        
        # Log budget status
        if elapsed_budget > ITERATION_BUDGET_SECONDS:
            logger.warning("⏱ Iteration completed in %.2fs (budget: %ss, exceeded by %.1fs)", total_time, ITERATION_BUDGET_SECONDS, elapsed_budget - ITERATION_BUDGET_SECONDS)
        else:
            logger.debug("⏱ Iteration completed in %.2fs (budget: %ss, remaining: %.1fs)", total_time, ITERATION_BUDGET_SECONDS, budget_tracker.remaining())
        
        logger.info("✅ Анализ завершен за %.2f секунд", total_time)
        
        # Успешное выполнение
        system_state.reset_errors()
        system_state.increment_cycle(success=True)
        
        # ИНВАРИАНТ: Периодически сохраняем snapshot (каждые 5 циклов)
        if system_state.performance_metrics.total_cycles % 5 == 0:
            try:
                from core.signal_snapshot_store import SystemStateSnapshotStore
                from database import cleanup_old_snapshots
                snapshot = system_state.create_snapshot()
                # Используем SystemStateSnapshotStore - entry point с fault injection
                await asyncio.to_thread(SystemStateSnapshotStore.save, snapshot)
                # Очищаем старые snapshot'ы (оставляем последние 10)
                await asyncio.to_thread(cleanup_old_snapshots, keep_last_n=10)
                # Трассы решений — не старше 90 дней (6.4). Отдельный try: сбой
                # очистки не должен выглядеть как ошибка сохранения снимка.
                try:
                    from core.decision_trace import prune_decision_trace
                    await asyncio.to_thread(prune_decision_trace, 90)
                except Exception as prune_err:
                    logger.warning("Очистка трасс решений не удалась: %s", prune_err)
            except IOError as e:
                # Обработка fault injection из storage layer
                if "FAULT_INJECTION: storage_failure" in str(e):
                    logger.error(
                        "FAULT_INJECTION: storage_failure - Controlled exception from storage layer. Runtime continues. error_type=IOError error_message=%s",
                        e
                    )
                    # Записываем ошибку для health tracking
                    system_state.record_error("FAULT_INJECTION: storage_failure")
                    
                    # HARDENING: Проверяем safe-mode активацию через state machine
                    state_machine = get_state_machine()
                    if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        if not state_machine.is_safe_mode:
                            await state_machine.transition_to(
                                SystemStateEnum.SAFE_MODE,
                                reason=f"Storage fault injection: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                                owner="main_startup",
                                metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                            )
                            logger.warning(
                                "SAFE-MODE activated after storage fault injection: consecutive_errors=%s >= MAX_CONSECUTIVE_ERRORS=%s",
                                system_state.system_health.consecutive_errors, MAX_CONSECUTIVE_ERRORS
                            )
                else:
                    # Другие IOError - логируем как обычную ошибку
                    logger.warning("⚠️ Ошибка сохранения snapshot: %s", e)
            except Exception as e:
                logger.warning("⚠️ Ошибка сохранения snapshot: %s", e)
        
        return True
        
    except asyncio.TimeoutError:
        # TimeoutError из любых операций в run_market_analysis()
        # Мягкое предупреждение, не авария
        logger.warning(
            "⏱ Analysis iteration exceeded timeout. Continuing with degraded mode."
        )
        # Не активируем safe_mode - продолжаем работу
        # Записываем для метрик, но не блокируем анализ
        system_state.record_error("Analysis timeout (non-critical)")
        # НЕ пробрасываем TimeoutError дальше - возвращаем False для продолжения цикла
        return False
        
    except Exception as e:
        error_msg = f"Критическая ошибка в цикле анализа: {type(e).__name__}: {e}"
        error_trace = traceback.format_exc()
        
        # Определяем, является ли это fault injection
        is_fault_injection = (
            isinstance(e, RuntimeError) and 
            "FAULT_INJECTION: decision_exception" in str(e)
        )
        
        if is_fault_injection:
            # Структурированное логирование для fault injection
            logger.error(
                "FAULT_INJECTION: decision_exception - Controlled exception injected for resilience testing. Runtime continues normally. error_type=%s error_message=%s",
                type(e).__name__, e
            )
        else:
            logger.error("%s\n%s", error_msg, error_trace)

        system_state.record_error(str(e))

        # HARDENING: Включаем safe-mode при множественных ошибках через state machine
        state_machine = get_state_machine()
        if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            if not state_machine.is_safe_mode:
                await state_machine.transition_to(
                    SystemStateEnum.SAFE_MODE,
                    reason=f"Consecutive errors threshold: {system_state.system_health.consecutive_errors} >= {MAX_CONSECUTIVE_ERRORS}",
                    owner="error_alert",
                    metadata={"consecutive_errors": system_state.system_health.consecutive_errors}
                )
                logger.warning(
                    "SAFE-MODE activated: consecutive_errors=%s >= MAX_CONSECUTIVE_ERRORS=%s. Trading blocked for safety.",
                    system_state.system_health.consecutive_errors, MAX_CONSECUTIVE_ERRORS
                )
        
        # Отправляем уведомление
        try:
            await asyncio.wait_for(
                asyncio.to_thread(error_alert, f"{error_msg}\n\nТрассировка:\n{error_trace[:500]}"),
                timeout=10.0
            )
        except Exception:
            logger.warning("Failed to send error alert for critical analysis error", exc_info=True)

        return False


async def market_analysis_loop():
    """
    Основной цикл анализа рынка.
    Запускается строго каждые ANALYSIS_INTERVAL секунд без накопления дрейфа.
    
    Использует абсолютное планирование по monotonic clock для предотвращения дрейфа.
    
    Features:
    - Абсолютное планирование (без дрейфа)
    - Мягкий контроль времени (без аварий)
    - Метрики производительности
    - Алерты при медленном анализе
    - Graceful shutdown support
    """
    # GLOBAL STATE (intentional)
    logger.info("Market analysis loop started")
    liveness.mark("analysis")  # отсчёт до первого оборота — от старта цикла
    
    # Use shutdown_event for proper cancellation semantics
    shutdown_evt = get_shutdown_event()
    
    # ========== АБСОЛЮТНОЕ ПЛАНИРОВАНИЕ ==========
    # Используем monotonic clock для предотвращения дрейфа
    # Адаптивный интервал: увеличивается при ошибках, уменьшается при стабильной работе
    current_interval = float(ANALYSIS_INTERVAL)
    next_run = time.monotonic()
    
    # ========== АДАПТИВНАЯ СИСТЕМА ==========
    # Отслеживание состояния для адаптации
    adaptive_state = {
        "stable_cycles": 0,  # Количество успешных циклов подряд
        "last_safe_mode_state": system_state.system_health.safe_mode,
        "last_trading_paused_state": system_state.system_health.trading_paused,
        "safe_mode_exit_time": None,  # Время выхода из safe_mode
    }
    
    # Инициализируем адаптивный интервал
    if _adaptive_system_state["adaptive_interval"] is None:
        _adaptive_system_state["adaptive_interval"] = float(ANALYSIS_INTERVAL)
    
    # ========== МЕТРИКИ ==========
    metrics = {
        "analysis_count": 0,
        "analysis_total_time": 0.0,
        "analysis_max_time": 0.0,
        "start_time": time.monotonic(),
        "last_metrics_log": time.monotonic(),
    }
    
    # Инициализируем глобальные метрики при первом запуске
    if _analysis_metrics["start_time"] is None:
        update_analysis_metrics({"start_time": metrics["start_time"]})
    
    # ========== ALERT ESCALATION ==========
    # Alert evaluation теперь выполняется в evaluate_and_send_alerts()
    # с дедупликацией через _alert_last_sent
    
    while system_state.system_health.is_running and not shutdown_evt.is_set():
        try:
            # Запоминаем время начала анализа
            start = time.monotonic()
            
            # Выполняем анализ
            success = await run_market_analysis()

            # ========== POSITION TRACKER POLLING (Phase 2) ==========
            # Опрашиваем открытые позиции на бирже (только TESTNET/LIVE)
            try:
                from trading_mode import get_trading_mode, TradingMode
                _mode = get_trading_mode()
                if _mode in (TradingMode.TESTNET, TradingMode.LIVE):
                    from execution.position_tracker import get_position_tracker
                    from database import close_position_by_order_id, insert_pnl_record
                    _tracker = get_position_tracker()
                    if _tracker.active_count() > 0:
                        _poll = await asyncio.to_thread(_tracker.poll)
                        for _closed in _poll.just_closed:
                            # Журнал закрывается по фактическому PnL биржи (closed-pnl),
                            # а не по последнему нереализованному из опроса.
                            from execution.exchange_ledger import record_close
                            _pnl = await asyncio.to_thread(record_close, _closed)
                            logger.info(
                                "[TRACKER] Position closed: %s %s pnl=%.2f",
                                _closed.symbol, _closed.side, _pnl,
                            )
                            _t = asyncio.create_task(
                                send_message_async(
                                    f"📉 Позиция закрыта: {_closed.symbol} {_closed.side}\n"
                                    f"💰 PnL: {_pnl:+.2f} USDT"
                                ),
                                name="TelegramNotify",
                            )
                            RUNNING_TASKS.add(_t)
                            _t.add_done_callback(RUNNING_TASKS.discard)
            except Exception as _e:
                logger.warning("Position tracker poll error: %s: %s", type(_e).__name__, _e)

            # Вычисляем длительность анализа
            duration = time.monotonic() - start
            
            # ========== ОБНОВЛЕНИЕ МЕТРИК ==========
            metrics["analysis_count"] += 1
            metrics["analysis_total_time"] += duration
            metrics["analysis_max_time"] = max(metrics["analysis_max_time"], duration)
            
            # Обновляем глобальные метрики для health endpoint
            update_analysis_metrics({
                "analysis_count": metrics["analysis_count"],
                "analysis_total_time": metrics["analysis_total_time"],
                "analysis_max_time": metrics["analysis_max_time"],
                "last_analysis_duration": duration,
            })
            
            # ========== PROMETHEUS METRICS (NON-BLOCKING) ==========
            # Записываем длительность в histogram
            record_analysis_duration(duration)
            # Увеличиваем счетчик завершенных циклов
            increment_analysis_cycles()
            liveness.mark("analysis")  # оборот цикла завершён — метка для healthcheck
            
            # ========== АДАПТИВНАЯ СИСТЕМА ==========
            # Получаем текущее состояние для адаптации
            consecutive_errors = system_state.system_health.consecutive_errors
            adaptive_system = get_adaptive_system_state()
            volatility_state = adaptive_system["volatility_state"]
            
            # 1. Адаптивный интервал анализа (на основе волатильности и ошибок)
            if ADAPTIVE_INTERVAL_ENABLED:
                # Базовый интервал из глобального состояния
                base_interval = _adaptive_system_state["adaptive_interval"]
                
                # Корректировка на основе волатильности
                volatility_multiplier = 1.0
                if volatility_state == "LOW":
                    # Низкая волатильность - увеличиваем интервал (1.5-2.0)
                    volatility_multiplier = 1.75  # Среднее значение
                elif volatility_state == "MEDIUM":
                    # Средняя волатильность - без изменений
                    volatility_multiplier = 1.0
                elif volatility_state == "HIGH":
                    # Высокая волатильность - уменьшаем интервал (0.7-0.8)
                    volatility_multiplier = 0.75  # Среднее значение
                
                # Применяем множитель волатильности
                volatility_adjusted_interval = base_interval * volatility_multiplier
                
                # Корректировка на основе ошибок (как раньше)
                if success and consecutive_errors == 0:
                    # Успешный цикл без ошибок - увеличиваем счетчик стабильности
                    adaptive_state["stable_cycles"] += 1
                    # Если достаточно стабильных циклов - уменьшаем базовый интервал
                    if adaptive_state["stable_cycles"] >= ADAPTIVE_STABLE_CYCLES and base_interval > ADAPTIVE_INTERVAL_MIN:
                        old_base = base_interval
                        base_interval = max(ADAPTIVE_INTERVAL_MIN, base_interval / ADAPTIVE_INTERVAL_MULTIPLIER)
                        if base_interval < old_base:
                            logger.info("📉 Adaptive base interval decreased: %.0fs → %.0fs (stable cycles: %s)", old_base, base_interval, adaptive_state['stable_cycles'])
                            adaptive_state["stable_cycles"] = 0
                else:
                    # Есть ошибки - увеличиваем базовый интервал
                    adaptive_state["stable_cycles"] = 0
                    if consecutive_errors > 0:
                        old_base = base_interval
                        base_interval = min(ADAPTIVE_INTERVAL_MAX, base_interval * ADAPTIVE_INTERVAL_MULTIPLIER)
                        if base_interval > old_base:
                            logger.info("📈 Adaptive base interval increased: %.0fs → %.0fs (errors: %s)", old_base, base_interval, consecutive_errors)
                
                # Обновляем базовый интервал в глобальном состоянии
                _adaptive_system_state["adaptive_interval"] = base_interval
                
                # Пересчитываем интервал с учетом волатильности (после обновления base_interval)
                volatility_adjusted_interval = base_interval * volatility_multiplier
                
                # Финальный интервал с учетом волатильности (clamp между min и max)
                current_interval = max(ADAPTIVE_INTERVAL_MIN, min(ADAPTIVE_INTERVAL_MAX, volatility_adjusted_interval))
            else:
                # Адаптивный интервал отключен - используем базовую логику на основе ошибок
                if success and consecutive_errors == 0:
                    adaptive_state["stable_cycles"] += 1
                    if adaptive_state["stable_cycles"] >= ADAPTIVE_STABLE_CYCLES and current_interval > ADAPTIVE_INTERVAL_MIN:
                        old_interval = current_interval
                        current_interval = max(ADAPTIVE_INTERVAL_MIN, current_interval / ADAPTIVE_INTERVAL_MULTIPLIER)
                        if current_interval < old_interval:
                            logger.info("📉 Adaptive interval decreased: %.0fs → %.0fs (stable cycles: %s)", old_interval, current_interval, adaptive_state['stable_cycles'])
                            adaptive_state["stable_cycles"] = 0
                else:
                    adaptive_state["stable_cycles"] = 0
                    if consecutive_errors > 0:
                        old_interval = current_interval
                        current_interval = min(ADAPTIVE_INTERVAL_MAX, current_interval * ADAPTIVE_INTERVAL_MULTIPLIER)
                        if current_interval > old_interval:
                            logger.info("📈 Adaptive interval increased: %.0fs → %.0fs (errors: %s)", old_interval, current_interval, consecutive_errors)
            
            # 2. Auto-resume: выход из SAFE_MODE после AUTO_RESUME_SUCCESS_CYCLES чистых
            # оборотов подряд (решение владельца 11.09.2026). Раньше счётчик обнулялся на
            # каждом обороте, пока поднят флаг safe_mode, и из защитного режима выводил
            # только TTL → FATAL → перезапуск, терявший ручную паузу.
            # Ручная пауза (/pause, провал сверки на старте, CRITICAL-алерт) автоматически
            # НЕ снимается: при ней SAFE_MODE снимается, а торговля остаётся на паузе.
            manual_pause = _control_plane_state.get("manual_pause_active", False)

            if AUTO_RESUME_TRADING_ENABLED:
                if system_state.system_health.safe_mode:
                    if success and consecutive_errors == 0:
                        _adaptive_system_state["recovery_cycles"] += 1
                        remaining = AUTO_RESUME_SUCCESS_CYCLES - _adaptive_system_state["recovery_cycles"]
                        if remaining > 0:
                            logger.info("🔄 Recovery progress: %s/%s clean cycles in SAFE_MODE", _adaptive_system_state['recovery_cycles'], AUTO_RESUME_SUCCESS_CYCLES)
                        else:
                            state_machine = get_state_machine()
                            recovered = await exit_safe_mode_via_recovery(
                                reason=f"Auto-resume: {AUTO_RESUME_SUCCESS_CYCLES} successful recovery cycles",
                                owner="market_analysis_loop"
                            )
                            state_machine.sync_to_system_state(system_state, manual_pause_active=manual_pause)
                            _adaptive_system_state["recovery_cycles"] = 0
                            if not recovered:
                                logger.warning("Recovery from SAFE_MODE refused by the state machine")
                            else:
                                if manual_pause:
                                    logger.info("✅ SAFE_MODE cleared after %s clean cycles; manual pause stays", AUTO_RESUME_SUCCESS_CYCLES)
                                    text = (f"✅ **Safe mode cleared**\n\nSystem recovered after {AUTO_RESUME_SUCCESS_CYCLES} "
                                            "clean analysis cycles. Trading stays paused manually — /resume to continue.")
                                else:
                                    logger.info("🔄 Trading auto-resumed after %s successful cycles", AUTO_RESUME_SUCCESS_CYCLES)
                                    text = (f"✅ **Trading resumed**\n\nSystem recovered after {AUTO_RESUME_SUCCESS_CYCLES} "
                                            "successful analysis cycles. Trading is now active.")
                                _t = asyncio.create_task(send_message_async(text), name="TelegramNotify")
                                RUNNING_TASKS.add(_t)
                                _t.add_done_callback(RUNNING_TASKS.discard)
                    else:
                        # Неудачный оборот в SAFE_MODE — отсчёт восстановления с нуля
                        if _adaptive_system_state["recovery_cycles"] > 0:
                            logger.info("🔄 Recovery reset: unclean cycle (was %s/%s)", _adaptive_system_state['recovery_cycles'], AUTO_RESUME_SUCCESS_CYCLES)
                        _adaptive_system_state["recovery_cycles"] = 0
                else:
                    # Не в SAFE_MODE: восстанавливать нечего, ручную паузу автоматически не снимаем
                    if _adaptive_system_state["recovery_cycles"] > 0:
                        _adaptive_system_state["recovery_cycles"] = 0
                    # Флаг ручной паузы без самой паузы — устаревший, снимаем
                    if manual_pause and not system_state.system_health.trading_paused:
                        _control_plane_state["manual_pause_active"] = False
            else:
                # Auto-resume отключен - используем старую логику на основе safe_mode exit
                if adaptive_state["last_safe_mode_state"] and not system_state.system_health.safe_mode:
                    # Выход из safe_mode
                    adaptive_state["safe_mode_exit_time"] = time.monotonic()
                    logger.info("✅ Safe mode deactivated - monitoring for auto-resume")
                
                if (adaptive_state["safe_mode_exit_time"] is not None and 
                    system_state.system_health.trading_paused and
                    not system_state.system_health.safe_mode):
                    # Проверяем, прошло ли достаточно времени после выхода из safe_mode
                    time_since_exit = time.monotonic() - adaptive_state["safe_mode_exit_time"]
                    if time_since_exit >= AUTO_RESUME_SAFE_MODE_DELAY:
                        # HARDENING: Автоматически возобновляем торговлю через state machine
                        state_machine = get_state_machine()
                        state_machine.sync_to_system_state(system_state, manual_pause_active=_control_plane_state.get("manual_pause_active", False))
                        adaptive_state["safe_mode_exit_time"] = None
                        logger.info("🔄 Trading auto-resumed after safe_mode exit (delay: %ss)", AUTO_RESUME_SAFE_MODE_DELAY)
                        # Отправляем уведомление
                        _t = asyncio.create_task(
                            send_message_async("✅ **Trading resumed**\n\nSystem recovered from safe mode. Trading is now active."),
                            name="TelegramNotify",
                        )
                        RUNNING_TASKS.add(_t)
                        _t.add_done_callback(RUNNING_TASKS.discard)
            
            # Обновляем состояние для следующей итерации
            adaptive_state["last_safe_mode_state"] = system_state.system_health.safe_mode
            adaptive_state["last_trading_paused_state"] = system_state.system_health.trading_paused
            
            # ========== МЯГКИЙ КОНТРОЛЬ ВРЕМЕНИ ==========
            # Заменяем аварийный watchdog на мягкое предупреждение
            if duration > MAX_ANALYSIS_TIME:
                logger.warning(
                    "⏱ Analysis slow: %.2fs (limit %.2fs)",
                    duration,
                    MAX_ANALYSIS_TIME
                )
            
            # ========== ALERT ESCALATION (NON-BLOCKING) ==========
            # Оцениваем и отправляем алерты асинхронно, не блокируя analysis loop
            # Создаём задачу для алертов (не ждём её завершения)
            # CRITICAL: Wrap in exception handler to prevent silent failures
            async def _safe_evaluate_alerts():
                """Wrapper to ensure alert evaluation errors are logged"""
                try:
                    await evaluate_and_send_alerts(duration)
                except asyncio.CancelledError:
                    logger.debug("Alert evaluation task cancelled")
                    raise
                except Exception as e:
                    logger.error(
                        "Alert evaluation task failed: %s: %s", type(e).__name__, e,
                        exc_info=True
                    )
            
            alert_task = asyncio.create_task(_safe_evaluate_alerts(), name="AlertEvaluation")
            # Fire-and-forget task inside a registered loop; cancelled when MarketAnalysis is cancelled.
            # Add done-callback to surface any unexpected exceptions.
            def _alert_task_done(t: asyncio.Task) -> None:
                if not t.cancelled():
                    exc = t.exception()
                    if exc:
                        logger.error(
                            "AlertEvaluation task failed: %s: %s", type(exc).__name__, exc,
                            exc_info=exc,
                        )
            alert_task.add_done_callback(_alert_task_done)
            
            # ========== ПЕРИОДИЧЕСКОЕ ЛОГИРОВАНИЕ МЕТРИК ==========
            now = time.monotonic()
            if (now - metrics["last_metrics_log"]) >= METRICS_LOG_INTERVAL:
                if metrics["analysis_count"] > 0:
                    avg = metrics["analysis_total_time"] / metrics["analysis_count"]
                    uptime = now - metrics["start_time"]
                    # Улучшенное логирование с адаптивной информацией
                    mode_status = "SAFE_MODE" if system_state.system_health.safe_mode else ("CAUTION" if consecutive_errors > 0 else "NORMAL")
                    trading_status = "PAUSED" if system_state.system_health.trading_paused else "ACTIVE"
                    logger.info(
                        "📈 Metrics | runs=%d avg=%.2fs max=%.2fs uptime=%.0fs interval=%.0fs mode=%s trading=%s errors=%d",
                        metrics["analysis_count"],
                        avg,
                        metrics["analysis_max_time"],
                        uptime,
                        current_interval,
                        mode_status,
                        trading_status,
                        consecutive_errors
                    )
                    metrics["last_metrics_log"] = now
            
            if not success:
                if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    pause_msg = f"Multiple errors ({system_state.system_health.consecutive_errors}). Pausing {ERROR_PAUSE}s"
                    logger.warning(pause_msg)
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(error_alert, pause_msg),
                            timeout=10.0
                        )
                    except Exception:
                        logger.warning("Failed to send error alert for pause notification", exc_info=True)

                    # Проверяем shutdown во время паузы
                    # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                    try:
                        shutdown_evt = get_shutdown_event()
                        remaining = ERROR_PAUSE
                        while remaining > 0:
                            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                                break
                            # Спим по 1 секунде, чтобы можно было прервать при shutdown
                            await asyncio.sleep(min(1.0, remaining))
                            remaining -= 1.0
                    except asyncio.CancelledError:
                        break
                    
                    system_state.reset_errors()
                    # После паузы сбрасываем next_run для корректного планирования
                    next_run = time.monotonic()
                else:
                    # Короткая пауза после ошибки (с проверкой shutdown)
                    # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
                    try:
                        shutdown_evt = get_shutdown_event()
                        remaining = 30
                        while remaining > 0:
                            if shutdown_evt.is_set() or not system_state.system_health.is_running:
                                break
                            # Спим по 1 секунде, чтобы можно было прервать при shutdown
                            await asyncio.sleep(min(1.0, remaining))
                            remaining -= 1.0
                    except asyncio.CancelledError:
                        break
                    # После паузы сбрасываем next_run для корректного планирования
                    next_run = time.monotonic()
            else:
                # ========== АБСОЛЮТНОЕ ПЛАНИРОВАНИЕ ==========
                # Вычисляем время до следующего запуска
                next_run += current_interval
                sleep_time = max(0.0, next_run - time.monotonic())
                
                # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
                shutdown_evt = get_shutdown_event()
                remaining = sleep_time
                while remaining > 0 and not shutdown_evt.is_set() and system_state.system_health.is_running:
                    # В SAFE_MODE — не дольше SAFE_MODE_RECOVERY_INTERVAL, в том числе если
                    # защитный режим включился посреди ожидания
                    if system_state.system_health.safe_mode and remaining > SAFE_MODE_RECOVERY_INTERVAL:
                        remaining = SAFE_MODE_RECOVERY_INTERVAL
                        next_run = time.monotonic() + remaining
                    chunk = min(1.0, remaining)
                    await asyncio.sleep(chunk)
                    remaining -= chunk
                
                # Проверяем shutdown после sleep
                if shutdown_evt.is_set() or not system_state.system_health.is_running:
                    break
                
        except asyncio.CancelledError:
            logger.info("Market analysis loop cancelled")
            break
        except Exception as e:
            logger.error("Critical error in market analysis loop: %s: %s", type(e).__name__, e)
            logger.error(traceback.format_exc())
            # Пауза с проверкой shutdown
            # Используем await asyncio.sleep() с проверкой shutdown каждую секунду
            try:
                shutdown_evt = get_shutdown_event()
                remaining = ERROR_PAUSE
                while remaining > 0:
                    if shutdown_evt.is_set() or not system_state.system_health.is_running:
                        break
                    # Спим по 1 секунде, чтобы можно было прервать при shutdown
                    await asyncio.sleep(min(1.0, remaining))
                    remaining -= 1.0
            except asyncio.CancelledError:
                break
            # После паузы сбрасываем next_run для корректного планирования
            next_run = time.monotonic()
    
    # Финальный лог метрик
    if metrics["analysis_count"] > 0:
        avg = metrics["analysis_total_time"] / metrics["analysis_count"]
        uptime = time.monotonic() - metrics["start_time"]
        logger.info(
            "📈 Final metrics | runs=%d avg=%.2fs max=%.2fs uptime=%.0fs",
            metrics["analysis_count"],
            avg,
            metrics["analysis_max_time"],
            uptime
        )
    
    logger.info("Market analysis loop stopped")
