"""
Оценка сетапа по свечам — чистая функция (Ф1 плана трейдера, docs/TRADER_PLAN.md, шаг 2).

Вынесена из signal_generator.generate_signals_for_symbols без изменения логики: живой
генератор и проверка на истории (backtest/) вызывают одну и ту же функцию — иначе
проверка на истории проверяла бы не тот код, что торгует. Побочные эффекты (журнал,
размер позиции, микроструктура, новизна, гейткипер, кэш цены, лог мониторинга) остаются
в генераторе. Эквивалентность выноса — tests/test_setup_equivalence.py: эталон снят с
кода до выноса.
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from adaptive_rr import calculate_adaptive_rr, calculate_volatility_pct
from candle_analysis import get_candle_analysis
from config import TIMEFRAMES
from context_engine import determine_state
from correlation_analysis import get_correlation_score
from indicators import (
    adx, atr, bollinger_bands, ema_crossover, macd, momentum, rsi, stochastic, trend_strength, volume_analysis,
)
from risk import calculate_stop_distance, enhanced_risk_level, risk_level
from scoring import calculate_score, get_entry_conditions, market_mode
from states import is_flat, market_direction
from volatility_filter import calculate_volatility_metrics, get_volatility_score

logger = logging.getLogger(__name__)

MIN_RR = 1.5
_manager = None


@dataclass
class Setup:
    """Сетап к сделке: всё, что генератору нужно дальше (размер, snapshot, гейткипер)."""
    side: str
    entry: float
    stop: float
    target: float
    strategy_name: str
    risk: str
    score: float
    mode: str
    states: Dict
    directions: Dict
    reasons: List
    score_details: Dict
    volatility_metrics: Dict
    correlation_data: Dict
    momentum_data: Dict
    atr_15m: float
    atr_5m: float
    volatility_pct: float
    candle_analysis: Dict


@dataclass
class Skip:
    """Сетапа нет. journal — поля для журнала сигналов, если этот отсев журналируется."""
    code: str
    reason: str
    journal: Optional[Dict] = None


def _default_manager():
    global _manager
    if _manager is None:
        from strategies.strategy_manager import StrategyManager
        _manager = StrategyManager()
    return _manager


def evaluate_setup(symbol: str, candles_map: Dict, *, market_correlations: Dict, good_time: bool,
                   market_regime=None, strategy_manager=None) -> Union[Setup, Skip]:
    """
    Оценить символ по свечам {таймфрейм: [свечи Bybit от старых к новым]}.

    market_regime — объект режима рынка (trend_type) или None; strategy_manager —
    экземпляр StrategyManager (по умолчанию общий для модуля).
    """
    strategy_manager = strategy_manager or _default_manager()
    states = {}
    directions = {}

    # Определяем состояния для каждого таймфрейма
    for tf, interval in TIMEFRAMES.items():
        candles = candles_map.get(tf, [])
        if not candles:
            logger.debug("No data for %s %s", symbol, tf)
            continue

        atr_val = atr(candles)
        # determine_state() возвращает MarketState enum (A/B/C/D) или None
        # None означает, что состояние не определено (валидный результат)
        states[tf] = determine_state(candles, atr_val)

        if tf in ["30m", "1h", "4h"]:
            directions[tf] = market_direction(candles)

    # Проверяем наличие необходимых данных для анализа
    if "15m" not in candles_map or not candles_map["15m"]:
        logger.debug("No 15m data for %s, skipping", symbol)
        return Skip("no_15m", "нет свечей 15m")

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
        return Skip("volatility", f"волатильность {volatility_metrics.get('volatility_level', 'UNKNOWN')} не для торговли")

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
        return Skip("mode_stop", "режим рынка STOP")

    # Базовая оценка риска с учетом 4h
    base_risk = risk_level(states, directions=directions)
    direction_4h = directions.get("4h", "FLAT")
    logger.debug("%s: base_risk=%s state_15m=%s 4h=%s", symbol, base_risk, states.get("15m"), direction_4h)

    # Проверяем объемы для фильтрации.
    # Генератор читал «volume_profile», которого get_candle_analysis не отдаёт (объём у неё —
    # на верхнем уровне): volume_trend всегда NORMAL, фильтр ниже не срабатывает, в оценку
    # риска объём приходит нейтральным (найдено 11.09.2026). Поведение сохранено ради
    # эквивалентности; включать ли фильтр объёма — решение по данным (Ф3 плана трейдера).
    candle_analysis = get_candle_analysis(candles_map.get("15m", []))
    volume_profile = candle_analysis.get("volume_profile", {})
    volume_trend = volume_profile.get("volume_trend", "NORMAL")

    # Пропускаем сигналы с низкой ликвидностью
    if volume_trend == "LOW":
        logger.debug("%s: low liquidity, skipping", symbol)
        return Skip("low_liquidity", "низкая ликвидность")

    # Рассчитываем параметры входа
    if not candles_map.get("5m") or len(candles_map["5m"]) == 0:
        return Skip("no_5m", "нет свечей 5m")
    last_5m = candles_map["5m"][-1]

    # ATR нужен и стратегиям, и fallback-логике
    atr_15m = atr(candles_map["15m"])
    atr_5m = atr(candles_map["5m"])
    volatility_pct = calculate_volatility_pct(candles_map["15m"])
    trend_strength_val = momentum_data.get("trend_strength_30m", 50) if momentum_data else 50

    # ── Multi-Strategy Engine ──
    # Стратегии имеют приоритет; если ни одна не сработала — fallback на старую логику.
    strategy_signal = None
    strategy_name = None
    try:
        vol_level = volatility_metrics.get("volatility_level", "MEDIUM")
        regime = "RANGE"
        if market_regime:
            regime = getattr(market_regime, "trend_type", "RANGE") or "RANGE"
            # "MIXED" from MarketRegimeBrain = ambiguous, treat as RANGE
            if regime not in ("TREND", "RANGE"):
                regime = "RANGE"

        # C-7: If regime is still RANGE (default), derive from ADX
        if regime == "RANGE" and momentum_data:
            _adx_data = momentum_data.get("adx_15m", {})
            _adx_val = _adx_data.get("adx", 0) if isinstance(_adx_data, dict) else 0
            if _adx_val > 25:
                regime = "TREND"
            # elif _adx_val < 15: confirmed RANGE, keep as is

        strategy_signal = strategy_manager.get_best_signal(
            symbol, candles_map, directions, momentum_data, states,
            market_regime=regime, volatility_level=vol_level,
        )
    except Exception as e:
        logger.warning("%s: strategy engine error: %s", symbol, e)

    if strategy_signal:
        # Стратегия дала сигнал — используем её entry/stop/target/side
        side = strategy_signal.side
        entry = strategy_signal.entry
        stop = strategy_signal.stop
        target = strategy_signal.target
        strategy_name = strategy_signal.strategy_name

        risk_distance = abs(entry - stop)
        rr_ratio = abs(target - entry) / risk_distance if risk_distance else 0
        # Run risk assessment even for strategy signals (C-T4 fix)
        stop_info_strat = calculate_stop_distance(entry, stop, atr_15m, entry)
        volume_info_strat = {"volume_trend": volume_trend, "volume_ratio": volume_profile.get("volume_ratio", 1.0)}
        risk = enhanced_risk_level(
            states, stop_info=stop_info_strat, volume_info=volume_info_strat,
            momentum_data=momentum_data, candles_map=candles_map, directions=directions
        )
        if risk == "HIGH":
            logger.info("%s: strategy %s signal rejected — HIGH risk", symbol, strategy_name)
            return Skip("high_risk", f"стратегия {strategy_name}: риск HIGH", journal=dict(
                symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                risk=risk, score=score, strategy=strategy_name, mode=mode))
        logger.info(
            "%s: STRATEGY %s → %s entry=%.4f stop=%.4f target=%.4f R:R=%.2f conf=%.2f",
            symbol, strategy_name, side, entry, stop, target, rr_ratio, strategy_signal.confidence,
        )
    else:
        # ── Fallback: старая entry_conditions логика ──
        entry_conditions = get_entry_conditions(states, directions, score_details)
        if not entry_conditions:
            logger.debug("%s: no strategy signal and no entry conditions, skipping", symbol)
            return Skip("no_entry_conditions", "нет сигнала стратегии и условий входа")

        logger.debug("%s: fallback entry conditions: %s, volume=%s", symbol, ", ".join(entry_conditions), volume_trend)

        entry = float(last_5m[4])
        high = float(last_5m[2])
        low = float(last_5m[3])

        bias = directions.get("30m", "FLAT")

        # HARD GATE: не открываем LONG если макро-тренд (1h/4h) DOWN, и наоборот.
        direction_1h = directions.get("1h", "FLAT")
        macro_trend = direction_4h if direction_4h != "FLAT" else direction_1h

        if bias == "UP" and macro_trend == "DOWN":
            logger.debug("%s: LONG blocked — macro trend DOWN (1h=%s 4h=%s)", symbol, direction_1h, direction_4h)
            return Skip("macro_trend", "LONG против нисходящего макротренда")
        if bias == "DOWN" and macro_trend == "UP":
            logger.debug("%s: SHORT blocked — macro trend UP (1h=%s 4h=%s)", symbol, direction_1h, direction_4h)
            return Skip("macro_trend", "SHORT против восходящего макротренда")

        if bias == "DOWN":
            side = "SHORT"
            stop = high
        elif bias == "UP":
            side = "LONG"
            stop = low
        else:
            logger.debug("%s: bias FLAT, no direction, skipping", symbol)
            return Skip("bias_flat", "нет направления на 30m")

        # Расширяем стоп если он слишком близко к entry.
        min_stop_dist = max(atr_15m * 1.0, entry * 0.003)
        if side == "LONG" and (entry - stop) < min_stop_dist:
            stop = entry - min_stop_dist
        elif side == "SHORT" and (stop - entry) < min_stop_dist:
            stop = entry + min_stop_dist

        # Проверяем размер стопа
        stop_info = calculate_stop_distance(entry, stop, atr_15m, entry)
        if not stop_info.get("is_valid", True):
            logger.debug("%s: invalid stop distance (%.2f%%), skipping", symbol, stop_info.get("stop_distance_pct", 0))
            return Skip("invalid_stop", "недопустимое расстояние до стопа")

        # Оценка риска
        volume_info = {"volume_trend": volume_trend, "volume_ratio": volume_profile.get("volume_ratio", 1.0)}
        risk = enhanced_risk_level(
            states, stop_info=stop_info, volume_info=volume_info,
            momentum_data=momentum_data, candles_map=candles_map, directions=directions
        )
        if risk == "HIGH":
            logger.debug("%s: high risk, skipping", symbol)
            return Skip("high_risk_fallback", "риск HIGH")

        # Адаптивный R:R
        rr_result = calculate_adaptive_rr(
            entry, stop, atr_15m, atr_5m,
            volatility_pct, trend_strength_val, risk
        )
        target = rr_result["target"]

        if rr_result["rr_ratio"] < MIN_RR:
            logger.debug("%s: R:R %.2f < %.1f minimum, skipping", symbol, rr_result["rr_ratio"], MIN_RR)
            return Skip("low_rr", f"R:R {rr_result['rr_ratio']:.2f} < {MIN_RR}", journal=dict(
                symbol=symbol, side=side, entry=entry, stop=stop, target=target, states=states,
                risk=risk, score=score, strategy="legacy", mode=mode))

        strategy_name = "legacy"

    return Setup(side=side, entry=entry, stop=stop, target=target, strategy_name=strategy_name, risk=risk,
                 score=score, mode=mode, states=states, directions=directions, reasons=reasons,
                 score_details=score_details, volatility_metrics=volatility_metrics,
                 correlation_data=correlation_data, momentum_data=momentum_data, atr_15m=atr_15m,
                 atr_5m=atr_5m, volatility_pct=volatility_pct, candle_analysis=candle_analysis)
