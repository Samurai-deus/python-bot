"""
Автоматический запуск и перезапуск торгового бота
Обеспечивает непрерывную работу с автоматическим восстановлением при ошибках
"""
import asyncio
import logging
import sys
import traceback
from datetime import datetime, UTC, timedelta
from pathlib import Path
import os

# Импорты для работы бота
from error_alert import error_alert
from telegram_bot import send_message
from health_monitor import send_heartbeat, HEARTBEAT_INTERVAL
from daily_report import generate_daily_report

# Импорты для анализа рынка (будем вызывать напрямую)
from config import SYMBOLS, TIMEFRAMES
from data_loader import get_candles_parallel
from time_filter import is_good_time
from correlation_analysis import analyze_market_correlations
from spike_alert import check_all_symbols_for_spikes
from signal_generator import generate_signals_for_symbols

# Экосистема
from core.decision_core import get_decision_core
from brains.market_regime_brain import get_market_regime_brain
from brains.risk_exposure_brain import get_risk_exposure_brain
from brains.cognitive_filter import get_cognitive_filter
from brains.opportunity_awareness import get_opportunity_awareness
from execution.gatekeeper import get_gatekeeper

# Настройки
BASE_DIR = Path(__file__).parent.absolute()
LOG_FILE = os.environ.get("LOG_FILE", str(BASE_DIR / "runner.log"))
ANALYSIS_INTERVAL = int(os.environ.get("BOT_INTERVAL", "300"))  # 5 минут
MAX_CONSECUTIVE_ERRORS = int(os.environ.get("MAX_CONSECUTIVE_ERRORS", "5"))
ERROR_PAUSE = int(os.environ.get("ERROR_PAUSE", "600"))  # 10 минут

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


# Импортируем SystemState
from system_state import SystemState

# Создаем единое состояние системы
system_state = SystemState()

# Устанавливаем глобальный экземпляр для доступа из telegram_commands
from system_state import set_system_state
set_system_state(system_state)


def log_to_file(message: str):
    """Логирует сообщение в файл"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(UTC)} - {message}\n")
    except Exception:
        pass


async def run_market_analysis():
    """
    Выполняет один цикл анализа рынка.
    Это async версия того, что делал main.py
    """
    import time
    
    start_time = time.time()
    logger.info(f"🚀 Начало анализа {len(SYMBOLS)} символов")
    
    # Проверка торгового времени
    if not is_good_time():
        logger.info("⏸ Не торговое время - пропускаем цикл")
        return True
    
    try:
        # Инициализация экосистемы
        logger.info("🧠 Инициализация экосистемы...")
        decision_core = get_decision_core()
        market_regime_brain = get_market_regime_brain()
        risk_exposure_brain = get_risk_exposure_brain()
        cognitive_filter = get_cognitive_filter()
        opportunity_awareness = get_opportunity_awareness()
        gatekeeper = get_gatekeeper()
        
        # Параллельная загрузка данных (синхронная операция в отдельном потоке)
        logger.info("📥 Параллельная загрузка данных...")
        load_start = time.time()
        # Используем asyncio.to_thread для синхронных операций с timeout
        try:
            all_candles = await asyncio.wait_for(
                asyncio.to_thread(get_candles_parallel, SYMBOLS, TIMEFRAMES, 120, 20),
                timeout=60.0
            )
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут загрузки данных с Bybit API (60 сек)")
            system_state.record_error("Timeout: загрузка данных с Bybit API")
            return False
        load_time = time.time() - load_start
        logger.info(f"✅ Данные загружены за {load_time:.2f} секунд")
        
        # Анализ "мозгами" экосистемы (синхронные операции в потоках)
        # Brain'ы обновляют SystemState напрямую, не через DecisionCore
        logger.info("🧠 Анализ Market Regime Brain...")
        try:
            market_regime = await asyncio.wait_for(
                asyncio.to_thread(market_regime_brain.analyze, SYMBOLS, all_candles, system_state),
                timeout=30.0
            )
            logger.info(f"   Режим: {market_regime.trend_type}, Волатильность: {market_regime.volatility_level}, Risk: {market_regime.risk_sentiment}")
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Market Regime Brain (30 сек)")
            market_regime = None
        except Exception as e:
            logger.error(f"⚠️ Ошибка в Market Regime Brain: {type(e).__name__}: {e}")
            market_regime = None
        
        logger.info("🧠 Анализ Risk & Exposure Brain...")
        try:
            risk_exposure = await asyncio.wait_for(
                asyncio.to_thread(risk_exposure_brain.analyze, SYMBOLS, all_candles, system_state),
                timeout=30.0
            )
            logger.info(f"   Риск: {risk_exposure.total_risk_pct:.2f}%, Позиций: {risk_exposure.active_positions}, Перегрузка: {risk_exposure.is_overloaded}")
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Risk & Exposure Brain (30 сек)")
            risk_exposure = None
        except Exception as e:
            logger.error(f"⚠️ Ошибка в Risk & Exposure Brain: {type(e).__name__}: {e}")
            risk_exposure = None
        
        logger.info("🧠 Анализ Cognitive Filter...")
        try:
            cognitive_state = await asyncio.wait_for(
                asyncio.to_thread(cognitive_filter.analyze, system_state),
                timeout=30.0
            )
            logger.info(f"   Пере-торговля: {cognitive_state.overtrading_score:.2f}, Пауза: {cognitive_state.should_pause}")
        except asyncio.TimeoutError:
            logger.error("⏱ Таймаут анализа Cognitive Filter (30 сек)")
            cognitive_state = None
        except Exception as e:
            logger.error(f"⚠️ Ошибка в Cognitive Filter: {type(e).__name__}: {e}")
            cognitive_state = None
        
        # Проверка через Decision Core (читает из SystemState)
        global_decision = decision_core.should_i_trade(system_state=system_state)
        if not global_decision.can_trade:
            logger.info(f"⏸ Decision Core блокирует торговлю: {global_decision.reason}")
            try:
                await asyncio.to_thread(send_message, f"🧠 Decision Core: {global_decision.reason}\n\nРекомендации:\n" + "\n".join(f"• {r}" for r in global_decision.recommendations))
            except Exception:
                pass
            return True
        
        # Проверка резких движений
        logger.info("🔍 Проверка резких движений...")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(check_all_symbols_for_spikes, SYMBOLS, all_candles),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            logger.warning("⏱ Таймаут проверки резких движений")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при проверке резких движений: {e}")
        
        # Анализ корреляций
        logger.info("📊 Анализ корреляций между парами...")
        try:
            market_correlations = await asyncio.wait_for(
                asyncio.to_thread(analyze_market_correlations, SYMBOLS, all_candles, "15m"),
                timeout=30.0
            )
            # Обновляем SystemState с корреляциями
            system_state.update_market_correlations(market_correlations)
        except asyncio.TimeoutError:
            logger.warning("⏱ Таймаут анализа корреляций")
            market_correlations = {}
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при анализе корреляций: {e}")
            market_correlations = {}
        
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
            logger.info(f"📊 Статистика сигналов: обработано {signal_stats['processed']}, отправлено {signal_stats['signals_sent']}, заблокировано {signal_stats['signals_blocked']}, ошибок {signal_stats['errors']}")
        except Exception as e:
            logger.error(f"⚠️ Ошибка при генерации сигналов: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
        
        # Статистика Gatekeeper
        gatekeeper_stats = gatekeeper.get_stats()
        if gatekeeper_stats["total"] > 0:
            logger.info(f"🚪 Gatekeeper: одобрено {gatekeeper_stats['approved']}, заблокировано {gatekeeper_stats['blocked']}")
        
        total_time = time.time() - start_time
        logger.info(f"✅ Анализ завершен за {total_time:.2f} секунд")
        
        # Успешное выполнение
        system_state.reset_errors()
        system_state.increment_cycle(success=True)
        
        # ИНВАРИАНТ: Периодически сохраняем snapshot (каждые 5 циклов)
        if system_state.performance_metrics.total_cycles % 5 == 0:
            try:
                from database import save_system_state_snapshot, cleanup_old_snapshots
                snapshot = system_state.create_snapshot()
                save_system_state_snapshot(snapshot)
                # Очищаем старые snapshot'ы (оставляем последние 10)
                cleanup_old_snapshots(keep_last_n=10)
            except Exception as e:
                logger.warning(f"⚠️ Ошибка сохранения snapshot: {e}")
        
        return True
        
    except Exception as e:
        error_msg = f"Критическая ошибка в цикле анализа: {type(e).__name__}: {e}"
        error_trace = traceback.format_exc()
        
        logger.error(f"{error_msg}\n{error_trace}")
        log_to_file(f"КРИТИЧЕСКАЯ ОШИБКА: {error_msg}\n{error_trace}")
        
        system_state.record_error(str(e))
        
        # Включаем safe-mode при множественных ошибках
        if system_state.system_health.consecutive_errors >= 3:
            system_state.system_health.safe_mode = True
            logger.warning("⚠️ Включен SAFE-MODE: торговля заблокирована из-за множественных ошибок")
        
        # Отправляем уведомление
        try:
            await asyncio.wait_for(
                asyncio.to_thread(error_alert, f"{error_msg}\n\nТрассировка:\n{error_trace[:500]}"),
                timeout=10.0
            )
        except Exception:
            pass
        
        return False


async def market_analysis_loop():
    """
    Основной цикл анализа рынка.
    Запускается каждые ANALYSIS_INTERVAL секунд.
    """
    logger.info("📊 Запуск цикла анализа рынка")
    
    while system_state.system_health.is_running:
        try:
            success = await run_market_analysis()
            
            if not success:
                if system_state.system_health.consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    pause_msg = f"⚠️ Множественные ошибки ({system_state.system_health.consecutive_errors}). Пауза {ERROR_PAUSE} секунд"
                    logger.warning(pause_msg)
                    log_to_file(pause_msg)
                    try:
                        await asyncio.to_thread(error_alert, pause_msg)
                    except Exception:
                        pass
                    await asyncio.sleep(ERROR_PAUSE)
                    system_state.reset_errors()
                else:
                    # Короткая пауза после ошибки
                    await asyncio.sleep(30)
            else:
                # Нормальная пауза между циклами
                await asyncio.sleep(ANALYSIS_INTERVAL)
                
        except asyncio.CancelledError:
            logger.info("⏹ Цикл анализа рынка остановлен")
            break
        except Exception as e:
            logger.error(f"Критическая ошибка в цикле анализа: {e}")
            logger.error(traceback.format_exc())
            await asyncio.sleep(ERROR_PAUSE)
    
    logger.info("📊 Цикл анализа рынка завершен")


async def heartbeat_loop():
    """
    Отправляет периодические heartbeat сообщения.
    """
    logger.info("💓 Запуск heartbeat мониторинга")
    
    while system_state.system_health.is_running:
        try:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await asyncio.to_thread(send_heartbeat)
            system_state.update_heartbeat()
            logger.info("💓 Heartbeat отправлен")
        except asyncio.CancelledError:
            logger.info("⏹ Heartbeat остановлен")
            break
        except Exception as e:
            logger.error(f"Ошибка в heartbeat loop: {e}")
            await asyncio.sleep(300)  # Пауза перед повтором
    
    logger.info("💓 Heartbeat завершен")


async def daily_report_loop():
    """
    Отправляет ежедневные отчеты в определенное время.
    """
    logger.info("📊 Запуск ежедневных отчетов")
    
    while system_state.system_health.is_running:
        try:
            # Вычисляем время до следующего отчета (00:00 UTC)
            now = datetime.now(UTC)
            next_report = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            sleep_seconds = (next_report - now).total_seconds()
            
            logger.info(f"📊 Следующий ежедневный отчет через {sleep_seconds/3600:.1f} часов")
            await asyncio.sleep(sleep_seconds)
            
            # Отправляем отчет
            await asyncio.to_thread(generate_daily_report)
            logger.info("📊 Ежедневный отчет отправлен")
            
        except asyncio.CancelledError:
            logger.info("⏹ Ежедневные отчеты остановлены")
            break
        except Exception as e:
            logger.error(f"Ошибка в daily report loop: {e}")
            await asyncio.sleep(3600)  # Пауза 1 час перед повтором
    
    logger.info("📊 Ежедневные отчеты завершены")


def start_telegram_commands_sync():
    """
    Запускает обработку Telegram команд в отдельном потоке.
    Это синхронная функция, которая создает свой event loop.
    
    ВАЖНО: Это ЕДИНСТВЕННОЕ место, где должен запускаться Telegram polling.
    """
    from telegram_bot import start_telegram_commands, is_telegram_polling_running
    
    # Проверяем, не запущен ли уже polling
    if is_telegram_polling_running():
        logger.warning("⚠️ Telegram polling уже запущен, пропускаем повторный запуск")
        return
    
    logger.info("🤖 Запуск Telegram команд...")
    # Запускаем в отдельном потоке (как и раньше)
    import threading
    thread = threading.Thread(target=start_telegram_commands, daemon=True, name="TelegramCommands")
    thread.start()
    logger.info("🤖 Telegram команды запущены в отдельном потоке")


async def main():
    """
    Главная функция - запускает все компоненты в одном процессе.
    
    ИНВАРИАНТ: SystemState создаётся ТОЛЬКО здесь.
    """
    logger.info("🚀 Запуск торгового бота")
    log_to_file("=== ЗАПУСК БОТА ===")
    
    # ИНВАРИАНТ: Восстанавливаем состояние из snapshot при старте
    try:
        from database import get_latest_system_state_snapshot
        snapshot = get_latest_system_state_snapshot()
        if snapshot:
            system_state.restore_from_snapshot(snapshot)
            logger.info("✅ Состояние восстановлено из snapshot")
        else:
            logger.info("ℹ️ Snapshot не найден, стартуем с пустым состоянием")
    except Exception as e:
        logger.warning(f"⚠️ Ошибка восстановления snapshot: {e}, стартуем с пустым состоянием")
    
    try:
        await asyncio.to_thread(send_message, "🚀 Торговый бот запущен")
    except Exception:
        pass
    
    # Запускаем все задачи параллельно
    tasks = [
        asyncio.create_task(market_analysis_loop(), name="MarketAnalysis"),
        asyncio.create_task(heartbeat_loop(), name="Heartbeat"),
        asyncio.create_task(daily_report_loop(), name="DailyReport"),
    ]
    
    # Запускаем Telegram команды в отдельном потоке
    # (они создают свой event loop, поэтому запускаем синхронно)
    start_telegram_commands_sync()
    
    logger.info("✅ Все компоненты запущены")
    
    try:
        # Ждем завершения всех задач (или KeyboardInterrupt)
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("⏹ Остановка бота по запросу пользователя")
        log_to_file("=== ОСТАНОВКА БОТА (KeyboardInterrupt) ===")
        system_state.system_health.is_running = False
        
        # Отменяем все задачи
        for task in tasks:
            task.cancel()
        
        # Ждем завершения задач
        await asyncio.gather(*tasks, return_exceptions=True)
        
        try:
            await asyncio.to_thread(send_message, "⏹ Торговый бот остановлен пользователем")
        except Exception:
            pass
    except Exception as e:
        error_msg = f"КРИТИЧЕСКАЯ ОШИБКА при запуске: {str(e)}"
        error_trace = traceback.format_exc()
        
        logger.critical(f"{error_msg}\n{error_trace}")
        log_to_file(f"КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ: {error_msg}\n{error_trace}")
        
        try:
            await asyncio.to_thread(error_alert, f"{error_msg}\n\nТрассировка:\n{error_trace[:500]}")
        except Exception:
            pass
        
        raise


if __name__ == "__main__":
    try:
        # Запускаем главный цикл
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("⏹ Остановка по запросу пользователя")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
        logger.critical(traceback.format_exc())
        sys.exit(1)
