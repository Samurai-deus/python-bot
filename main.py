from config import SYMBOLS, TIMEFRAMES
from data_loader import get_candles
from indicators import atr
from context_engine import determine_state
from states import entry_trigger_5m, market_direction, is_flat
from telegram_bot import send_message, send_chart
from risk import risk_level
from journal import log_signal
from state_cache import is_new_signal
from demo_trades import log_demo_trade
from time_filter import is_good_time
from scoring import calculate_score, market_mode
from signals import build_signal
from error_alert import error_alert
from monitor_log import log_monitor


for symbol in SYMBOLS:
    print(f"🔍 Проверяю символ: {symbol}")
    good_time = is_good_time()

    # Начало работы с символами
    try:
        if not good_time:
            continue

        states = {}
        candles_map = {}
        directions = {}

        for tf, interval in TIMEFRAMES.items():
            candles = get_candles(symbol, interval)  # получаем свечи для таймфрейма
            candles_map[tf] = candles
            log_monitor(symbol, tf)
            print(f"   ⏱ Таймфрейм: {tf}")
            atr_val = atr(candles)
            states[tf] = determine_state(candles, atr_val)

            if tf in ["30m", "1h"]:
                directions[tf] = market_direction(candles)

        flat = is_flat(candles_map["15m"], atr(candles_map["15m"]))
        score, reasons = calculate_score(states, directions, flat, good_time)
        mode = market_mode(score)

        # если рынок плохой — вообще молчим
        if mode == "STOP":
            continue

        risk = risk_level(states)

        # основной сценарий
        if states["15m"] == "D" and risk != "HIGH":
            if is_new_signal(symbol, states["15m"]):
                msg = build_signal(symbol, states, risk, directions)
                extra = (
                    f"\n\n📊 Score: {score}/100"
                    f"\n🧭 Режим: {mode}"
                    f"\nПричины:\n- " + "\n- ".join(reasons)
                )
                send_message(msg + extra)
                send_chart(symbol)
                log_signal(symbol, states, risk)

            # demo-логика (без изменений)
            trigger = entry_trigger_5m(candles_map["5m"])
            if trigger:
                last = candles_map["5m"][-1]
                entry = float(last[4])
                high = float(last[2])
                low = float(last[3])

                if directions.get("30m") == "DOWN":
                    side = "SHORT"
                    stop = high
                    target = entry - 2 * (stop - entry)
                elif directions.get("30m") == "UP":
                    side = "LONG"
                    stop = low
                    target = entry + 2 * (entry - stop)
                else:
                    continue

                log_demo_trade(symbol, side, entry, stop, target)

    except Exception as e:
        print(f"Ошибка при обработке символа {symbol}: {e}")

