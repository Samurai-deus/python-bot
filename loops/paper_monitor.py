"""
Мониторинг бумажных сделок: SL/TP по свежим свечам, закрытие и отчёт.

Вынесен из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 3). Состояние процесса приходит функцией get_state —
она отдаёт текущий system_state runner'а; событие остановки — параметром.
"""
import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def paper_trading_monitor_loop(get_state: Callable[[], Any], shutdown_evt: asyncio.Event):
    """
    Мониторинг бумажных сделок — проверяет SL/TP каждые 15 секунд.

    Независим от signal_generator: работает постоянно, пока есть открытые сделки.
    При достижении SL/TP — закрывает сделку и отправляет отчёт в Telegram.
    """
    logger.info("📄 Paper trading monitor started")
    from paper_fills import CandleWatermark
    paper_watermark = CandleWatermark()
    from trading_mode import sends_real_orders
    if sends_real_orders():
        # В TESTNET/LIVE сделки ведёт биржа, строки журнала закрывает трекер позиций
        # (execution/exchange_ledger.py). Задача остаётся живой: завершившуюся
        # задачу супервизор счёл бы сбоем.
        logger.info("📄 Бумажный монитор простаивает: режим с реальными ордерами")
        await shutdown_evt.wait()
        return

    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            from trade_manager import check_trades, get_open_trades
            from trade_reporter import generate_trade_report
            from data_loader import get_candles

            # База — в пуле потоков: синхронный запрос в async-функции держал весь цикл событий.
            open_trades = await asyncio.to_thread(get_open_trades)
            if open_trades:
                # Собираем уникальные символы с открытыми сделками
                symbols_to_check = list({t["symbol"] for t in open_trades})

                for symbol in symbols_to_check:
                    try:
                        candles = await asyncio.to_thread(get_candles, symbol, "5", 1)
                        if candles:
                            candle = candles[-1]
                            current_price = float(candle[4])  # close price
                            # Экстремумы — только появившиеся после прошлой проверки:
                            # минимум, случившийся до подтягивания трейлинга, раньше
                            # закрывал сделку по новому стопу задним числом.
                            fresh_low, fresh_high = paper_watermark.fresh_extremes(
                                symbol, candle[0], float(candle[3]), float(candle[2]))

                            # Обновляем кэш цен для WS snapshot (Mini App progress bar)
                            try:
                                import price_cache as _pc
                                _pc.update(symbol, current_price)
                            except Exception:
                                logger.debug("Failed to update price_cache for %s", symbol, exc_info=True)
                            closed = await asyncio.to_thread(
                                check_trades, symbol, current_price, low=fresh_low, high=fresh_high)
                            for closed_trade in closed:
                                trade_pnl = closed_trade.get("pnl", 0)
                                logger.info(
                                    "[PAPER] Trade closed: %s %s @ %.4f (%s) pnl=%.2f",
                                    symbol, closed_trade.get("side"), closed_trade.get("close_price", current_price),
                                    closed_trade.get("close_reason"), trade_pnl,
                                )
                                # Сбрасываем кэш сигнала И cooldown для символа,
                                # чтобы следующий цикл мог снова генерировать сигналы.
                                # reset_signal_cooldown clears both _trend_signal_timestamps
                                # (4h cooldown) and signal_cache (state dedup).
                                get_state().reset_signal_cooldown(symbol)
                                # Не отправляем отчёт если PnL фактически нулевой
                                # (breakeven exit или time exit при ~0 движении)
                                if abs(trade_pnl) < 0.01:
                                    logger.info("[PAPER] Skipping report for %s: PnL=%.4f (negligible)", symbol, trade_pnl)
                                    continue
                                try:
                                    await asyncio.to_thread(generate_trade_report, closed_trade)
                                except Exception as report_err:
                                    logger.warning("[PAPER] Failed to send trade report: %s", report_err)
                    except Exception as sym_err:
                        logger.warning("[PAPER] Error checking %s: %s", symbol, sym_err)

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("[PAPER] Monitor loop error: %s", e, exc_info=True)

        # Poll every 15s to catch flash crashes (was 60s — too slow)
        try:
            await asyncio.wait_for(shutdown_evt.wait(), timeout=15.0)
            break  # shutdown_evt сработал
        except asyncio.TimeoutError:
            pass  # Нормальный timeout — продолжаем цикл

    logger.info("📄 Paper trading monitor stopped")
