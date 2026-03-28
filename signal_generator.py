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
from journal import log_signal
from scoring import calculate_score, market_mode, get_entry_conditions
from monitor_log import log_monitor
from capital import position_size
from leverage import calculate_leverage
from candle_analysis import get_candle_analysis
from trade_manager import check_trades
from trade_reporter import generate_trade_report
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
from datetime import datetime, UTC
import logging

logger = logging.getLogger(__name__)


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
        "errors": 0
    }

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

            # Определяем состояния для каждого таймфрейма
            for tf, interval in TIMEFRAMES.items():
                candles = candles_map.get(tf, [])
                if not candles:
                    logger.debug("No data for %s %s", symbol, tf)
                    continue

                log_monitor(symbol, tf)
                atr_val = atr(candles)
                # determine_state() возвращает MarketState enum (A/B/C/D) или None
                # None означает, что состояние не определено (валидный результат)
                states[tf] = determine_state(candles, atr_val)

                if tf in ["30m", "1h", "4h"]:
                    directions[tf] = market_direction(candles)

            # Проверяем наличие необходимых данных для анализа
            if "15m" not in candles_map or not candles_map["15m"]:
                logger.debug("No 15m data for %s, skipping", symbol)
                continue
            
            flat = is_flat(candles_map["15m"], atr(candles_map["15m"]))
            
            # Анализ волатильности
            volatility_metrics = calculate_volatility_metrics(candles_map["15m"], period=20)
            volatility_score, volatility_reasons = get_volatility_score(volatility_metrics)
            
            # Проверка фильтра волатильности
            if not volatility_metrics.get("is_tradeable", True):
                logger.debug(
                    "%s: volatility %s (%.2f%%) not tradeable, skipping",
                    symbol, volatility_metrics.get("volatility_level", "UNKNOWN"), volatility_metrics.get("atr_pct", 0)
                )
                continue
            
            # Анализ корреляций
            correlation_data = market_correlations.get(symbol, {})
            correlation_score, correlation_reasons = get_correlation_score(market_correlations, symbol)
            
            # Рассчитываем все индикаторы
            momentum_data = {}
            if candles_map.get("15m"):
                try:
                    momentum_data["rsi_15m"] = rsi(candles_map["15m"], period=14)
                    momentum_data["macd_15m"] = macd(candles_map["15m"])
                    momentum_data["momentum_15m"] = momentum(candles_map["15m"])
                    momentum_data["bb_15m"] = bollinger_bands(candles_map["15m"], period=20)
                    momentum_data["stoch_15m"] = stochastic(candles_map["15m"], k_period=14)
                    momentum_data["adx_15m"] = adx(candles_map["15m"], period=14)
                    momentum_data["ema_cross_15m"] = ema_crossover(candles_map["15m"], fast_period=12, slow_period=26)
                    momentum_data["volume_15m"] = volume_analysis(candles_map["15m"], period=20)
                except Exception as e:
                    logger.warning("Indicator calc error 15m for %s: %s", symbol, e)
                    # Keep whatever was calculated before the error; do NOT reset momentum_data

            if candles_map.get("30m"):
                try:
                    momentum_data["trend_strength_30m"] = trend_strength(candles_map["30m"], period=20)
                    momentum_data["adx_30m"] = adx(candles_map["30m"], period=14)
                    momentum_data["ema_cross_30m"] = ema_crossover(candles_map["30m"], fast_period=12, slow_period=26)
                except Exception as e:
                    logger.warning("Indicator calc error 30m for %s: %s", symbol, e)
            
            # Улучшенная система оценки (добавляем волатильность и корреляции)
            score, reasons, score_details = calculate_score(
                states, directions, flat, good_time, 
                candles_map=candles_map, 
                momentum_data=momentum_data
            )
            
            # Добавляем баллы за волатильность и корреляции
            score += volatility_score
            reasons.extend(volatility_reasons)
            score += correlation_score
            reasons.extend(correlation_reasons)
            
            score_details["volatility_score"] = volatility_score
            score_details["correlation_score"] = correlation_score
            
            mode = market_mode(score)

            # Логируем состояние для отладки
            logger.debug(
                "%s: score=%s/125 mode=%s states=%s directions=%s",
                symbol, score, mode, states, directions
            )
            logger.debug(
                "%s: volatility=%s (%.2f%%) correlation=%s (avg=%.2f)",
                symbol,
                volatility_metrics.get("volatility_level", "UNKNOWN"),
                volatility_metrics.get("atr_pct", 0),
                correlation_data.get("market_alignment", "UNKNOWN"),
                correlation_data.get("avg_correlation", 0),
            )
            if momentum_data:
                logger.debug(
                    "%s: RSI=%.1f trend=%.1f%%",
                    symbol, momentum_data.get("rsi_15m", 0), momentum_data.get("trend_strength_30m", 0)
                )

            # если рынок плохой — вообще молчим
            if mode == "STOP":
                logger.debug("%s: mode=STOP, skipping", symbol)
                continue

            # Базовая оценка риска с учетом 4h
            base_risk = risk_level(states, directions=directions)
            direction_4h = directions.get("4h", "FLAT")
            logger.debug("%s: base_risk=%s state_15m=%s 4h=%s", symbol, base_risk, states.get("15m"), direction_4h)
            
            # Проверяем открытые сделки и закрываем при достижении TP/SL
            if candles_map.get("5m") and len(candles_map["5m"]) > 0:
                current_price = float(candles_map["5m"][-1][4])
                closed_trades = check_trades(symbol, current_price)
                
                # Отправляем отчеты по закрытым сделкам
                for closed_trade in closed_trades:
                    generate_trade_report(closed_trade)

            # Расширенные условия входа
            entry_conditions = get_entry_conditions(states, directions, score_details)
            
            if not entry_conditions:
                logger.debug("%s: no entry conditions, skipping", symbol)
                continue

            # Проверяем объемы для фильтрации
            candle_analysis = get_candle_analysis(candles_map.get("15m", []))
            volume_profile = candle_analysis.get("volume_profile", {})
            volume_trend = volume_profile.get("volume_trend", "NORMAL")

            # Пропускаем сигналы с низкой ликвидностью
            if volume_trend == "LOW":
                logger.debug("%s: low liquidity, skipping", symbol)
                continue

            logger.debug("%s: entry conditions: %s, volume=%s", symbol, ", ".join(entry_conditions), volume_trend)
            
            # Рассчитываем параметры входа
            if not candles_map.get("5m") or len(candles_map["5m"]) == 0:
                continue
            last_5m = candles_map["5m"][-1]
            entry = float(last_5m[4])
            high = float(last_5m[2])
            low = float(last_5m[3])
            
            bias = directions.get("30m", "FLAT")
            zone = None
            pos_size = None
            lev = None
            
            if bias == "DOWN":
                side = "SHORT"
                stop = high
            elif bias == "UP":
                side = "LONG"
                stop = low
            else:
                logger.debug("%s: bias FLAT, no direction, skipping", symbol)
                continue
            
            # Рассчитываем ATR для адаптивного R:R
            atr_15m = atr(candles_map["15m"])
            atr_5m = atr(candles_map["5m"])
            volatility_pct = calculate_volatility_pct(candles_map["15m"])
            trend_strength_val = momentum_data.get("trend_strength_30m", 50) if momentum_data else 50

            # Расширяем стоп если он слишком близко к entry.
            # Стоп от low/high последней 5m свечи часто слишком тесный в trending market.
            # Используем 1.5 ATR — крипта регулярно проходит 1 ATR внутри «шума»,
            # поэтому 1 ATR давал слишком частые стоп-ауты до отработки сигнала.
            min_stop_dist = max(atr_15m * 1.5, entry * 0.005)  # 1.5 ATR или 0.5% — что больше
            if side == "LONG" and (entry - stop) < min_stop_dist:
                stop = entry - min_stop_dist
                logger.debug("%s: stop widened to minimum: %.4f (%.2f%%)", symbol, stop, min_stop_dist / entry * 100)
            elif side == "SHORT" and (stop - entry) < min_stop_dist:
                stop = entry + min_stop_dist
                logger.debug("%s: stop widened to minimum: %.4f (%.2f%%)", symbol, stop, min_stop_dist / entry * 100)

            # Проверяем размер стопа
            stop_info = calculate_stop_distance(entry, stop, atr_15m, entry)
            if not stop_info.get("is_valid", True):
                logger.debug("%s: invalid stop distance (%.2f%%), skipping", symbol, stop_info.get("stop_distance_pct", 0))
                continue
            
            # Улучшенная оценка риска с учетом всех индикаторов
            volume_info = {"volume_trend": volume_trend, "volume_ratio": volume_profile.get("volume_ratio", 1.0)}
            risk = enhanced_risk_level(
                states, 
                stop_info=stop_info, 
                volume_info=volume_info,
                momentum_data=momentum_data,
                candles_map=candles_map,
                directions=directions
            )
            
            if risk == "HIGH":
                logger.debug("%s: high risk, skipping", symbol)
                continue
            
            # Адаптивный расчет R:R
            rr_result = calculate_adaptive_rr(
                entry, stop, atr_15m, atr_5m, 
                volatility_pct, trend_strength_val, risk
            )
            target = rr_result["target"]

            # Минимальный R:R = 1.5: без этого даже 60% win rate даёт убыток.
            # При R:R < 1.5 потенциальная награда не оправдывает риск — пропускаем.
            MIN_RR = 1.5
            if rr_result["rr_ratio"] < MIN_RR:
                logger.debug(
                    "%s: R:R %.2f < %.1f minimum, skipping signal",
                    symbol, rr_result["rr_ratio"], MIN_RR
                )
                continue

            zone = {"entry": entry, "stop": stop, "target": target}
            pos_size = position_size(entry, stop, side)
            lev = calculate_leverage(states, atr_15m, entry, stop, side)
            
            logger.debug(
                "%s: %s entry=%.4f stop=%.4f target=%.4f R:R=%.2f risk=%s %s",
                symbol, side, entry, stop, target, rr_result["rr_ratio"], risk, rr_result["reason"]
            )

            state_15m = states.get("15m", "")
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
                    "position_size": pos_size,
                    "leverage": lev,
                    "candle_analysis": candle_analysis,
                    "risk": risk,
                    "score": score,
                    "mode": mode,
                    "rr_ratio": rr_result['rr_ratio'],
                    "volatility_pct": volatility_pct
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
                        SignalSnapshotStore.save(snapshot)
                        stats["signals_sent"] += 1

                        # Открываем демо-сделку только если сигнал реально отправлен
                        try:
                            from demo_trades import log_demo_trade
                            zone = signal_data.get("zone")
                            if zone and entry and stop and target:
                                log_demo_trade(
                                    symbol, side, entry, stop, target,
                                    position_size=signal_data.get("position_size", pos_size),
                                    leverage=lev
                                )
                                logger.info("%s: demo trade opened", symbol)
                        except Exception as trade_error:
                            logger.warning(
                                "%s: failed to open demo trade: %s: %s",
                                symbol, type(trade_error).__name__, trade_error
                            )
                            # Не блокируем процесс, это не критично
                    else:
                        stats["signals_blocked"] += 1
                        logger.info("%s: signal blocked by Gatekeeper", symbol)
                except Exception as e:
                    logger.error(
                        "%s: error sending signal: %s: %s",
                        symbol, type(e).__name__, e, exc_info=True
                    )
                    stats["signals_blocked"] += 1
            else:
                logger.debug("%s: signal not new (state_15m=%s already sent), skipping", symbol, state_15m)

        except Exception as e:
            logger.error("Error processing symbol %s: %s", symbol, e, exc_info=True)
            stats["errors"] += 1
            # Продолжаем обработку других символов при ошибке
    
    return stats

