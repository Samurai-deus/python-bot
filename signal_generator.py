"""
Общий модуль для генерации торговых сигналов
Используется в main.py и ecosystem_main.py для устранения дублирования кода
"""
from config import SYMBOLS, TIMEFRAMES
from indicators import (
    atr, rsi, macd, momentum, trend_strength,
    bollinger_bands, stochastic, adx, ema_crossover, volume_analysis
)
from context_engine import determine_state
from states import market_direction, is_flat
from risk import risk_level, enhanced_risk_level, calculate_stop_distance
import journal
from scoring import calculate_score, market_mode, get_entry_conditions
from monitor_log import log_monitor
from capital import position_size
from leverage import calculate_leverage
from candle_analysis import get_candle_analysis
from adaptive_rr import calculate_adaptive_rr, calculate_volatility_pct
from volatility_filter import calculate_volatility_metrics, get_volatility_score
from correlation_analysis import get_correlation_score
from execution.gatekeeper import get_gatekeeper
from brains.opportunity_awareness import get_opportunity_awareness
from core.decision_core import get_decision_core
from core.signal_snapshot import (
    SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel,
    mode_to_decision, risk_string_to_enum, volatility_string_to_enum
)
from core.market_state import normalize_states_dict
from core.cognitive_engine import calculate_confidence, calculate_entropy
from strategies.strategy_manager import StrategyManager
from strategies.setup import Skip, evaluate_setup
from execution.sizing_guard import unaffordable_reason
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)

# Singleton: создаём один раз, используем для всех символов
_strategy_manager = StrategyManager()

# Символы, которые сейчас не кандидаты из-за минимального ордера: в лог — только смена.
_unaffordable = set()


def _position_cap_usd() -> float:
    """Предел одной позиции в долларах — тот же, которым её проверит Risk Core."""
    try:
        from capital import get_current_balance
        from core.risk_core import get_risk_core
        return get_current_balance() * get_risk_core().config.max_single_position_pct / 100.0
    except Exception:
        logger.warning("signal_generator: предел позиции не посчитан — отсев по минимальному ордеру "
                       "в этом цикле выключен", exc_info=True)
        return 0.0


def _journal(status, code, reason, *, mode=None, seen_by=None, **fields):
    """
    Сигнал-кандидат в журнал (шаг 3 плана обучения). Сбой журнала торговлю не трогает.
    seen_by — SystemState для отсева до проверки новизны: сетап, по которому сигнал уже
    был (то же состояние 15m), — не новый кандидат, а повтор, и в журнал не идёт.
    """
    try:
        if seen_by is not None and not seen_by.would_be_new_signal(
                fields["symbol"], (fields.get("states") or {}).get("15m", "")):
            return
        moment = datetime.now(UTC)
        if journal.record_signal(status=status, reason_code=code, reason=reason, timestamp=moment,
                                 decision=mode_to_decision(mode) if mode else None, **fields):
            _ai_review(status, moment, mode, fields)
    except Exception:
        logger.warning("Журнал сигналов: запись не удалась", exc_info=True)


def _ai_review(status, moment, mode, fields):
    """
    Невзятый сигнал — ИИ на оценку в тени (шаг 5 плана обучения): так видно, различает ли
    ИИ лучше фильтров системы. Метка — та же, что в журнале: по ней сверяется исход.
    """
    from ai_trader.worker import submit
    entry, stop, target = fields.get("entry"), fields.get("stop"), fields.get("target")
    rr = abs(target - entry) / abs(entry - stop) if entry and stop and target and entry != stop else None
    signal_data = {"side": fields.get("side"), "entry": entry, "stop": stop, "target": target, "rr_ratio": rr,
                   "score": fields.get("score"), "mode": mode, "risk": fields.get("risk"),
                   "strategy_name": fields.get("strategy")}
    submit(fields["symbol"], signal_data, None, fate=status, signal_ts=moment.isoformat())


def generate_signals_for_symbols(
    all_candles,
    market_correlations,
    good_time,
    decision_core=None,
    opportunity_awareness=None,
    gatekeeper=None,
    system_state=None
):
    """
    Генерирует торговые сигналы для всех символов.
    
    Args:
        all_candles: Словарь свечей по символам и таймфреймам
        market_correlations: Результаты анализа корреляций
        good_time: Флаг торгового времени
        decision_core: Экземпляр Decision Core (опционально)
        opportunity_awareness: Экземпляр Opportunity Awareness (опционально)
        gatekeeper: Экземпляр Gatekeeper (опционально)
    
    Returns:
        dict: Статистика по обработанным сигналам
    """
    if decision_core is None:
        decision_core = get_decision_core()
    if opportunity_awareness is None:
        opportunity_awareness = get_opportunity_awareness()
    if gatekeeper is None:
        gatekeeper = get_gatekeeper()
    
    if all_candles is None:
        logger.error("generate_signals_for_symbols: all_candles is None, skipping")
        return {"processed": 0, "signals_sent": 0, "signals_blocked": 0, "errors": 0}

    stats = {
        "processed": 0,
        "signals_sent": 0,
        "signals_blocked": 0,
        "errors": 0,
        "skipped_min_order": 0,
    }

    position_cap_usd = _position_cap_usd()

    for symbol in SYMBOLS:
        logger.debug("Checking symbol: %s", symbol)
        stats["processed"] += 1

        try:
            states = {}
            candles_map = all_candles.get(symbol, {})
            directions = {}

            # Проверяем, что данные загружены
            if not candles_map:
                logger.debug("No candle data for %s", symbol)
                continue

            # Минимальный ордер биржи больше предела одной позиции — сделку по символу
            # не открыть ни при каком сигнале. Свечи символа остаются в данных рынка,
            # пропускается только он сам как кандидат.
            last = (candles_map.get("5m") or candles_map.get("15m") or [None])[-1]
            skip_reason = unaffordable_reason(symbol, float(last[4]) if last else None, position_cap_usd)
            if skip_reason:
                stats["skipped_min_order"] += 1
                if symbol not in _unaffordable:
                    _unaffordable.add(symbol)
                    logger.info("%s: не кандидат для сделки — %s", symbol, skip_reason)
                continue
            if symbol in _unaffordable:
                _unaffordable.discard(symbol)
                logger.info("%s: снова кандидат — минимальный ордер укладывается в предел позиции", symbol)

            # Оценка сетапа — чистая функция strategies/setup.py (Ф1 плана трейдера): тот же код
            # вызывает проверка на истории. Здесь — только побочные эффекты генератора.
            for tf in TIMEFRAMES:
                if candles_map.get(tf):
                    log_monitor(symbol, tf)
            if candles_map.get("5m"):
                # Кэш последней цены (WS-снапшот берёт из него current_price)
                try:
                    import price_cache as _pc
                    _pc.update(symbol, float(candles_map["5m"][-1][4]))
                except Exception:
                    logger.error("Failed to update price_cache for %s", symbol, exc_info=True)
            regime_state = system_state.market_regime if system_state and hasattr(system_state, "market_regime") else None
            outcome = evaluate_setup(symbol, candles_map, market_correlations=market_correlations, good_time=good_time,
                                     market_regime=regime_state, strategy_manager=_strategy_manager)
            if isinstance(outcome, Skip):
                if outcome.journal is not None:
                    _journal(journal.SKIPPED, outcome.code, outcome.reason, **outcome.journal,
                             collapse_repeats=True, seen_by=system_state)
                continue
            states, directions = outcome.states, outcome.directions
            score, reasons, score_details, mode = outcome.score, outcome.reasons, outcome.score_details, outcome.mode
            volatility_metrics, correlation_data = outcome.volatility_metrics, outcome.correlation_data
            atr_15m, volatility_pct, candle_analysis = outcome.atr_15m, outcome.volatility_pct, outcome.candle_analysis
            side, entry, stop, target = outcome.side, outcome.entry, outcome.stop, outcome.target
            strategy_name, risk = outcome.strategy_name, outcome.risk

            zone = {"entry": entry, "stop": stop, "target": target}
            pos_size = position_size(entry, stop, side)
            if not pos_size:
                # Нет места в лимитах портфеля (число позиций, суммарный риск, экспозиция)
                # или размер меньше минимального ордера — сигнал не отправляется. До
                # 11.09.2026 он уходил с нулевым размером, и гейткипер записывал это как
                # сбой Risk Core («returned None → DENY + HALTED»).
                logger.info("%s: сигнал пропущен — размер позиции 0 (портфель заполнен или ниже минимума)", symbol)
                _journal(journal.SKIPPED, "no_room", "размер позиции 0: портфель заполнен или размер ниже минимума",
                         symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                         risk=risk, score=score, strategy=strategy_name, mode=mode, collapse_repeats=True, seen_by=system_state)
                continue
            lev = calculate_leverage(states, atr_15m, entry, stop, side)

            # ── Microstructure filter (OI + Funding) ──
            try:
                from market_data.bybit_market_data import get_open_interest, get_funding_rate
                from market_data.microstructure_analyzer import analyze_microstructure
                oi_data = get_open_interest(symbol)
                funding_data = get_funding_rate(symbol, limit=5)
                micro = analyze_microstructure(symbol, candles_map.get("15m", []), oi_data, funding_data)

                if micro.get("block_long") and side == "LONG":
                    logger.info("%s: LONG blocked by extreme positive funding", symbol)
                    _journal(journal.SKIPPED, "funding", "экстремальный фандинг против LONG",
                             symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                             risk=risk, score=score, strategy=strategy_name, mode=mode, collapse_repeats=True, seen_by=system_state)
                    continue
                if micro.get("block_short") and side == "SHORT":
                    logger.info("%s: SHORT blocked by extreme negative funding", symbol)
                    _journal(journal.SKIPPED, "funding", "экстремальный фандинг против SHORT",
                             symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                             risk=risk, score=score, strategy=strategy_name, mode=mode, collapse_repeats=True, seen_by=system_state)
                    continue
            except Exception as e:
                logger.debug("Microstructure unavailable for %s: %s", symbol, e)
                micro = {}

            risk_distance = abs(entry - stop)
            rr_ratio_final = abs(target - entry) / risk_distance if risk_distance else 0
            logger.debug(
                "%s: %s entry=%.4f stop=%.4f target=%.4f R:R=%.2f risk=%s strategy=%s",
                symbol, side, entry, stop, target, rr_ratio_final, risk, strategy_name,
            )

            state_15m = states.get("15m", "")

            from config import MAX_NEW_POSITIONS_PER_TURN
            if stats["signals_sent"] >= MAX_NEW_POSITIONS_PER_TURN:
                # Не больше N новых позиций за оборот (шаг 2б плана, 11.09.2026) — вместо паузы
                # 60 с между действиями Risk Core. Проверка стоит ДО is_new_signal: тот запоминает
                # состояние, и отложенный сигнал в следующий оборот был бы уже «не новым».
                if system_state is None or system_state.would_be_new_signal(symbol, state_15m):
                    stats["skipped_turn_limit"] = stats.get("skipped_turn_limit", 0) + 1
                    logger.info("%s: сигнал отложен — за оборот уже %d новых позиций (предел %d)",
                                symbol, stats["signals_sent"], MAX_NEW_POSITIONS_PER_TURN)
                    _journal(journal.SKIPPED, "turn_limit",
                             f"за оборот уже {stats['signals_sent']} новых позиций (предел {MAX_NEW_POSITIONS_PER_TURN})",
                             symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                             risk=risk, score=score, strategy=strategy_name, mode=mode, collapse_repeats=True, seen_by=system_state)
                continue
            # Используем SystemState для проверки нового сигнала
            is_new = system_state.is_new_signal(symbol, state_15m) if system_state else True
            logger.debug("%s: new signal check: state_15m=%s is_new=%s", symbol, state_15m, is_new)
            
            if is_new:
                # Анализ возможностей (обновляет SystemState напрямую)
                opportunity = opportunity_awareness.analyze(symbol, candles_map, system_state)
                
                # Создаём SignalSnapshot - immutable доменный объект
                # Нормализуем states перед созданием snapshot
                normalized_states = normalize_states_dict(states)
                
                # Получаем market_regime из system_state
                market_regime = system_state.market_regime if system_state else None
                
                # Преобразуем строковые значения в enum
                risk_enum = risk_string_to_enum(risk)
                volatility_enum = volatility_string_to_enum(volatility_metrics.get("volatility_level"))
                decision = mode_to_decision(mode)
                
                # Формируем decision_reason
                score_max = 125  # Максимальный возможный score
                decision_reason = f"Score: {score}/{score_max}, Mode: {mode}, Risk: {risk}"
                if reasons:
                    decision_reason += f", Reasons: {', '.join(reasons[:3])}"
                
                # Создаём временный snapshot для вычисления confidence и entropy
                # (они требуют полного snapshot для расчёта)
                temp_snapshot = SignalSnapshot(
                    timestamp=datetime.now(UTC),
                    symbol=symbol,
                    timeframe_anchor="15m",
                    states=normalized_states,
                    market_regime=market_regime,
                    volatility_level=volatility_enum,
                    correlation_level=correlation_data.get("avg_correlation") if correlation_data else None,
                    score=score,
                    score_max=score_max,
                    risk_level=risk_enum,
                    recommended_leverage=lev,
                    entry=entry,
                    tp=target,
                    sl=stop,
                    side=side,
                    decision=decision,
                    decision_reason=decision_reason,
                    directions=directions,
                    score_details=score_details,
                    reasons=reasons,
                    confidence=0.0,  # Временное значение
                    entropy=0.0    # Временное значение
                )
                
                # Вычисляем когнитивные метрики
                confidence = calculate_confidence(temp_snapshot)
                entropy = calculate_entropy(temp_snapshot)

                # ── Learning feedback loop ──
                try:
                    from brains.trade_learner import (
                        get_symbol_adjustment, get_confidence_calibration, should_skip_symbol,
                    )
                    if should_skip_symbol(symbol):
                        logger.info("[Learning] Skipping %s — historically very low win rate", symbol)
                        _journal(journal.SKIPPED, "learner", "исторически очень низкая доля прибыльных",
                                 symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                                 risk=risk, score=score, strategy=strategy_name, mode=mode, confidence=confidence)
                        continue

                    learner_adj = get_symbol_adjustment(symbol, side) + get_confidence_calibration(confidence)
                    if learner_adj != 0.0:
                        logger.info(
                            "[Learning] %s %s: confidence %.3f %+.3f -> %.3f",
                            symbol, side, confidence, learner_adj,
                            max(0.05, min(1.0, confidence + learner_adj)),
                        )
                        confidence = max(0.05, min(1.0, confidence + learner_adj))
                except Exception as e:
                    logger.debug("[Learning] Unavailable for %s: %s", symbol, e)

                # Создаём финальный snapshot с вычисленными confidence и entropy
                snapshot = SignalSnapshot(
                    timestamp=datetime.now(UTC),
                    symbol=symbol,
                    timeframe_anchor="15m",
                    states=normalized_states,
                    market_regime=market_regime,
                    volatility_level=volatility_enum,
                    correlation_level=correlation_data.get("avg_correlation") if correlation_data else None,
                    score=score,
                    score_max=score_max,
                    confidence=confidence,
                    entropy=entropy,
                    risk_level=risk_enum,
                    recommended_leverage=lev,
                    entry=entry,
                    tp=target,
                    sl=stop,
                    side=side,
                    decision=decision,
                    decision_reason=decision_reason,
                    directions=directions,
                    score_details=score_details,
                    reasons=reasons
                )
                
                # Формируем данные сигнала для Gatekeeper (для обратной совместимости)
                signal_data = {
                    "zone": zone,
                    "side": side,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    # Стратегия — в журнал сделок (record_open берёт signal_data["strategy_name"]).
                    # До 11.09.2026 ключа не было, и у биржевых сделок стратегия была пустой.
                    "strategy_name": strategy_name,
                    "position_size": pos_size,
                    "leverage": lev,
                    "candle_analysis": candle_analysis,
                    "risk": risk,
                    "score": score,
                    "mode": mode,
                    "rr_ratio": abs(target - entry) / abs(stop - entry) if abs(stop - entry) > 0 else 0,
                    "volatility_pct": volatility_pct,
                    # ATR 15m — для отказа по устаревшему сигналу в гейткипере (3.15)
                    "atr": atr_15m,
                }
                
                logger.info("%s: sending signal via Gatekeeper", symbol)
                try:
                    # Используем Gatekeeper для отправки сигнала
                    signal_sent = gatekeeper.send_signal(
                        symbol=symbol,
                        signal_data=signal_data,
                        states=states,
                        directions=directions,
                        risk=risk,
                        score=score,
                        mode=mode,
                        reasons=reasons,
                        system_state=system_state,
                        snapshot=snapshot  # Передаём snapshot для портфельного анализа
                    )
                    logger.info("%s: signal processed by Gatekeeper", symbol)

                    if signal_sent:
                        # Логируем через SignalSnapshotStore - entry point с fault injection
                        from core.signal_snapshot_store import SignalSnapshotStore
                        SignalSnapshotStore.save(snapshot, strategy=strategy_name)
                        stats["signals_sent"] += 1

                        # Открываем демо-сделку только если сигнал реально отправлен
                        try:
                            from demo_trades import log_demo_trade
                            from trade_manager import get_open_trades
                            zone = signal_data.get("zone")
                            # Только размер, одобренный гейткипером: исходный pos_size
                            # не прошёл урезаний риска. Нет одобренного — нет сделки.
                            effective_pos_size = signal_data.get("approved_position_size")
                            already_open = any(
                                t["symbol"] == symbol for t in get_open_trades()
                            )
                            from trading_mode import sends_real_orders
                            if sends_real_orders():
                                # В TESTNET/LIVE строку журнала пишет гейткипер по факту
                                # исполненного ордера (3.6). Бумажная сделка рядом была бы
                                # вторым журналом: бумажный монитор закрывал бы её по своим
                                # уровням, пока реальная позиция жива.
                                logger.debug("%s: real-orders mode — ledger row is written by gatekeeper", symbol)
                            elif zone and entry and stop and target and effective_pos_size and not already_open:
                                log_demo_trade(
                                    symbol, side, entry, stop, target,
                                    position_size=effective_pos_size,
                                    leverage=lev,
                                    strategy_name=strategy_name,
                                )
                                logger.info("%s: demo trade opened [%s]", symbol, strategy_name)
                            elif already_open:
                                logger.info("%s: skipping demo trade — already has open position", symbol)
                            elif not effective_pos_size:
                                logger.info("%s: skipping demo trade — position_size is zero (no available capital)", symbol)
                        except Exception as trade_error:
                            logger.warning(
                                "%s: failed to open demo trade: %s: %s",
                                symbol, type(trade_error).__name__, trade_error
                            )
                            # Не блокируем процесс, это не критично
                    else:
                        stats["signals_blocked"] += 1
                        logger.info("%s: signal blocked by Gatekeeper", symbol)
                        code, why = getattr(gatekeeper, "last_block_reason", None) or ("gatekeeper", "причина не передана")
                        try:
                            if journal.log_signal_snapshot(snapshot, status=journal.BLOCKED, reason_code=code,
                                                           reason=why, strategy=strategy_name):
                                from ai_trader.worker import submit as ai_submit
                                ai_submit(symbol, signal_data, snapshot, fate=journal.BLOCKED)
                        except Exception:
                            logger.warning("Журнал сигналов: запись не удалась", exc_info=True)
                except Exception as e:
                    logger.error(
                        "%s: error sending signal: %s: %s",
                        symbol, type(e).__name__, e, exc_info=True
                    )
                    stats["signals_blocked"] += 1
                    try:
                        journal.log_signal_snapshot(snapshot, status=journal.BLOCKED, reason_code="error",
                                                    reason=f"{type(e).__name__}: {e}", strategy=strategy_name)
                    except Exception:
                        logger.warning("Журнал сигналов: запись не удалась", exc_info=True)
            else:
                logger.debug("%s: signal not new (state_15m=%s already sent), skipping", symbol, state_15m)

        except Exception as e:
            logger.error("Error processing symbol %s: %s", symbol, e, exc_info=True)
            stats["errors"] += 1
            # Продолжаем обработку других символов при ошибке
    
    return stats

