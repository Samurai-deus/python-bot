"""
Инструменты внедрения сбоев для проверки устойчивости: синтетический тик
решений и «внедрение зависания» цикла событий. По умолчанию выключены
(ENABLE_SYNTHETIC_DECISION_TICK, FAULT_INJECT_LOOP_STALL в runner.py).

Вынесены из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 4). Состояние процесса приходит функцией get_state,
событие остановки, флаг включения и числовые параметры — аргументами.

Известное ограничение (не исправлено при переносе): «внедрение зависания» на
деле цикл событий не блокирует — оно спит короткими asyncio.sleep, как и
сказано в его комментариях, поэтому пропуск heartbeat им не воспроизводится.
"""
import asyncio
import logging
from datetime import datetime, UTC
from typing import Any, Callable

from system_state_machine import SystemState as SystemStateEnum, get_state_machine

logger = logging.getLogger(__name__)


async def synthetic_decision_tick_loop(get_state: Callable[[], Any], shutdown_evt: asyncio.Event,
                                       enabled: bool, interval: float, max_consecutive_errors: int):
    """
    Synthetic decision tick - периодически выполняет decision pipeline
    с синтетическим SignalSnapshot для тестирования устойчивости.

    Используется для:
    - Тестирования fault injection
    - Валидации decision pipeline без внешних сигналов
    - Проверки health handling

    Без side effects: NO orders, NO persistence, NO Telegram.
    """
    if not enabled:
        return  # Не запускаем если ENV не установлен

    logger.info("Synthetic decision tick loop started (interval: %ss)", interval)

    from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel
    from core.market_state import MarketState
    from core.decision_core import MarketRegime
    from execution.gatekeeper import get_gatekeeper

    tick_count = 0

    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            # Sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
            remaining = interval
            while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
                await asyncio.sleep(min(1.0, remaining))
                remaining -= 1.0

            # Проверяем shutdown после sleep
            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break

            tick_count += 1

            # Создаём синтетический SignalSnapshot
            synthetic_snapshot = SignalSnapshot(
                timestamp=datetime.now(UTC),
                symbol="BTCUSDT",  # Используем BTCUSDT как тестовый символ
                timeframe_anchor="15m",
                states={
                    "5m": MarketState.A,
                    "15m": MarketState.D,
                    "30m": MarketState.A,
                    "1h": MarketState.B,
                    "4h": MarketState.A
                },
                market_regime=MarketRegime(
                    trend_type="TREND",
                    volatility_level="MEDIUM",
                    risk_sentiment="RISK_ON",
                    confidence=0.7
                ),
                volatility_level=VolatilityLevel.NORMAL,
                correlation_level=0.5,
                score=75,
                score_max=125,
                confidence=0.65,
                entropy=0.35,
                risk_level=RiskLevel.MEDIUM,
                recommended_leverage=5.0,
                entry=50000.0,
                tp=51000.0,
                sl=49500.0,
                decision=SignalDecision.ENTER,
                decision_reason="SYNTHETIC_DECISION_TICK: synthetic signal for testing",
                directions={"15m": "UP", "30m": "UP", "1h": "UP", "4h": "UP"},
                score_details={},
                reasons=["Synthetic tick for decision pipeline testing"]
            )

            logger.info(
                "SYNTHETIC_DECISION_TICK: executing decision pipeline (tick=%d, symbol=%s)",
                tick_count, synthetic_snapshot.symbol
            )

            gatekeeper = get_gatekeeper()

            # Решение проходит внутренние проверки гейткипера БЕЗ отправки в Telegram.
            try:
                # 1. MetaDecisionBrain (если доступен)
                meta_result = None
                if gatekeeper.meta_decision_brain:
                    meta_result = gatekeeper._check_meta_decision(synthetic_snapshot, get_state())
                    if meta_result and not meta_result.allow_trading:
                        logger.info(
                            "SYNTHETIC_DECISION_TICK: MetaDecisionBrain BLOCKED (reason=%s)",
                            meta_result.reason
                        )
                        continue  # Переходим к следующему tick

                # 2. DecisionCore.should_i_trade() - здесь может быть fault injection
                try:
                    decision_core_result = gatekeeper.decision_core.should_i_trade(
                        symbol=synthetic_snapshot.symbol,
                        system_state=get_state()
                    )

                    if not decision_core_result.can_trade:
                        logger.info(
                            "SYNTHETIC_DECISION_TICK: DecisionCore BLOCKED (reason=%s)",
                            decision_core_result.reason
                        )
                        continue

                    logger.debug(
                        "SYNTHETIC_DECISION_TICK: DecisionCore ALLOWED (reason=%s)",
                        decision_core_result.reason
                    )
                except RuntimeError as e:
                    # Обработка fault injection
                    if "FAULT_INJECTION: decision_exception" in str(e):
                        logger.error(
                            "SYNTHETIC_DECISION_TICK: FAULT_INJECTION detected - Controlled exception from "
                            "DecisionCore. Runtime continues. error_type=RuntimeError error_message=%s",
                            e
                        )
                        # Записываем ошибку для health tracking
                        get_state().record_error("FAULT_INJECTION: decision_exception (synthetic tick)")

                        # HARDENING: Проверяем safe-mode активацию через state machine
                        state_machine = get_state_machine()
                        consecutive_errors = get_state().system_health.consecutive_errors
                        if consecutive_errors >= max_consecutive_errors:
                            if not state_machine.is_safe_mode:
                                await state_machine.transition_to(
                                    SystemStateEnum.SAFE_MODE,
                                    reason="SYNTHETIC_DECISION_TICK: consecutive_errors >= MAX_CONSECUTIVE_ERRORS",
                                    owner="synthetic_decision_tick_loop",
                                    metadata={"consecutive_errors": consecutive_errors}
                                )
                                logger.warning(
                                    "SYNTHETIC_DECISION_TICK: SAFE-MODE activated - consecutive_errors=%s >= "
                                    "MAX_CONSECUTIVE_ERRORS=%s",
                                    consecutive_errors, max_consecutive_errors
                                )
                    else:
                        # Другие RuntimeError - пробрасываем
                        raise

                # 3. PortfolioBrain
                portfolio_analysis = gatekeeper._check_portfolio(synthetic_snapshot)
                if portfolio_analysis:
                    from core.portfolio_brain import PortfolioDecision
                    if portfolio_analysis.decision == PortfolioDecision.BLOCK:
                        logger.info(
                            "SYNTHETIC_DECISION_TICK: PortfolioBrain BLOCKED (reason=%s)",
                            portfolio_analysis.reason
                        )
                        continue

                # 4. PositionSizer
                if gatekeeper.position_sizer:
                    sizing_result = gatekeeper._calculate_position_size(
                        synthetic_snapshot,
                        portfolio_analysis
                    )
                    if sizing_result and not sizing_result.position_allowed:
                        logger.info(
                            "SYNTHETIC_DECISION_TICK: PositionSizer BLOCKED (reason=%s)",
                            sizing_result.reason
                        )
                        continue

                logger.debug(
                    "SYNTHETIC_DECISION_TICK: decision pipeline completed successfully (tick=%d)",
                    tick_count
                )

            except Exception as e:
                # Обработка ошибок в decision pipeline
                logger.error(
                    "SYNTHETIC_DECISION_TICK: error in decision pipeline (tick=%d): %s: %s",
                    tick_count, type(e).__name__, e,
                    exc_info=True
                )
                get_state().record_error(f"Synthetic tick error: {type(e).__name__}")

        except asyncio.CancelledError:
            logger.info("Synthetic decision tick loop cancelled")
            break
        except Exception as e:
            logger.error("Error in synthetic decision tick loop: %s: %s", type(e).__name__, e)
            # Пауза перед повтором
            try:
                await asyncio.wait_for(
                    asyncio.sleep(30),
                    timeout=30.0
                )
            except asyncio.CancelledError:
                break

    logger.info("Synthetic decision tick loop stopped (total ticks: %d)", tick_count)


async def loop_stall_injection_task(get_state: Callable[[], Any], shutdown_evt: asyncio.Event,
                                    enabled: bool, stall_seconds: float, startup_delay: float = 30.0):
    """
    Loop stall injection — задумано как преднамеренная блокировка event loop для
    проверки обнаружения зависания. Фактически цикл не блокирует (см. модуль).
    """
    if not enabled:
        return  # Не запускаем если ENV не установлен

    logger.info("Loop stall injection enabled (stall duration: %ss)", stall_seconds)

    # Ждём после старта, чтобы система успела инициализироваться;
    # sleep с проверкой shutdown каждую секунду для быстрого отклика на SIGTERM
    remaining = startup_delay
    while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
        await asyncio.sleep(min(1.0, remaining))
        remaining -= 1.0

    # Проверяем shutdown после sleep
    if shutdown_evt.is_set() or not get_state().system_health.is_running:
        return

    logger.warning(
        "FAULT_INJECTION: loop_stall starting - Event loop will be blocked for %ss. "
        "This is a controlled fault injection for testing.",
        stall_seconds
    )

    try:
        logger.warning("FAULT_INJECTION: loop_stall active - simulating event loop stall for %ss", stall_seconds)

        # asyncio.sleep короткими интервалами: цикл событий НЕ блокируется полностью,
        # создаётся только нагрузка (так было и в runner.py).
        remaining = stall_seconds
        while remaining > 0:
            if shutdown_evt.is_set() or not get_state().system_health.is_running:
                break
            await asyncio.sleep(min(0.1, remaining))
            remaining -= 0.1

        logger.info(
            "FAULT_INJECTION: loop_stall completed - Event loop should resume. Recovery expected."
        )

    except asyncio.CancelledError:
        logger.info("Loop stall injection cancelled")
    except Exception as e:
        logger.error("Error in loop stall injection: %s: %s", type(e).__name__, e)
