"""
Супервизор Telegram polling: изоляция сбоев Telegram от торгового процесса.

Вынесен из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 5). Состояние процесса приходит функцией get_state —
она отдаёт текущий system_state runner'а; событие остановки — параметром.
"""
import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


async def telegram_supervisor(get_state: Callable[[], Any], shutdown_evt: asyncio.Event):
    """
    Изолированный supervisor для Telegram polling.

    HARD FAULT ISOLATION:
    - Запускает polling в отдельной asyncio task
    - Отслеживает task и перехватывает ВСЕ исключения (включая из внутренних tasks)
    - Ловит telegram.error.NetworkError и telegram.error.Conflict
    - НИКОГДА не пробрасывает эти исключения наружу
    - Реализует exponential backoff (10s → 300s max)
    - Логирует "TELEGRAM_NETWORK_FAILURE"
    - Обновляет system_state.system_health (safe_mode, consecutive_errors)
    - Перезапускает polling после ошибок

    Runtime, market loop и heartbeat продолжают работать даже если
    Telegram полностью недоступен часами.

    ВАЖНО: Эта функция НИКОГДА не awaited в main loop.
    Запускается только через asyncio.create_task().
    """
    from telegram.ext import ApplicationBuilder
    from telegram.error import NetworkError, Conflict
    from telegram_bot import TOKEN
    from telegram_commands import setup_commands

    logger.info("📱 Telegram supervisor started")

    # Exponential backoff: 10s → 300s max
    backoff_seconds = 10.0
    MAX_BACKOFF = 300.0
    BACKOFF_MULTIPLIER = 1.5

    app = None
    polling_task = None

    while get_state().system_health.is_running and not shutdown_evt.is_set():
        try:
            # Build Telegram application
            if app is None:
                # HTTP-клиенты — только из telegram_bot.build_request: там прокси.
                # PTB держит ДВА клиента — для методов бота и для getUpdates. Если
                # второй не задать, PTB создаст его сам, без прокси, и на хосте,
                # где api.telegram.org напрямую недоступен, бот не получит ни одной
                # команды при исправно работающей отправке.
                from telegram_bot import build_request
                app = (
                    ApplicationBuilder()
                    .token(TOKEN)
                    .request(build_request())
                    .get_updates_request(build_request())
                    .build()
                )
                setup_commands(app)

            # Start polling
            logger.info("Starting Telegram polling...")
            # КРИТИЧНО: initialize() и start() могут блокировать на сетевом I/O при network blackhole
            # Обёртываем в wait_for с таймаутом для предотвращения блокировки shutdown
            try:
                await asyncio.wait_for(app.initialize(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Telegram app.initialize() timeout - network may be unreachable")
                raise  # Перезапустим с backoff
            except asyncio.CancelledError:
                raise  # Пробрасываем для правильного shutdown

            try:
                await asyncio.wait_for(app.start(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("Telegram app.start() timeout - network may be unreachable")
                # Cleanup initialize перед перезапуском
                try:
                    await asyncio.wait_for(app.shutdown(), timeout=2.0)
                except Exception:
                    logger.debug("Telegram app.shutdown failed during initialize timeout cleanup", exc_info=True)
                raise  # Перезапустим с backoff
            except asyncio.CancelledError:
                # Cleanup при cancellation
                try:
                    await asyncio.wait_for(app.shutdown(), timeout=2.0)
                except Exception:
                    logger.debug("Telegram app.shutdown failed during supervisor cancellation cleanup", exc_info=True)
                raise  # Пробрасываем для правильного shutdown

            # КРИТИЧНО: start_polling() - долгоживущая задача, запускаем её как task
            # и ждём shutdown event или cancellation, а не саму задачу
            async def _safe_polling():
                """Wrapper to ensure polling errors are logged"""
                try:
                    await app.updater.start_polling(
                        poll_interval=2.0,
                        drop_pending_updates=True,
                    )
                except asyncio.CancelledError:
                    logger.info("Telegram polling task cancelled")
                    raise
                except Exception as e:
                    logger.error(
                        "Telegram polling task failed: %s: %s",
                        type(e).__name__, e,
                        exc_info=True
                    )
                    raise

            polling_task = asyncio.create_task(_safe_polling(), name="TelegramPolling")
            logger.info("✅ Telegram polling started successfully")

            # Reset backoff on success
            backoff_seconds = 10.0

            # КРИТИЧНО: Ждём shutdown event или cancellation, а не polling_task;
            # polling_task будет отменён при shutdown через finally блок
            try:
                while get_state().system_health.is_running and not shutdown_evt.is_set():
                    # Проверяем, не завершилась ли polling_task (ошибка)
                    if polling_task.done():
                        try:
                            await polling_task  # Получаем исключение если есть
                        except (NetworkError, Conflict):
                            # NetworkError - это нормально, перезапустим
                            raise
                        except Exception as e:
                            # Другие исключения - логируем и перезапускаем
                            logger.warning("Telegram polling task completed with error: %s: %s", type(e).__name__, e)
                            raise

                    # КРИТИЧНО: sleep с проверкой cancellation для быстрого отклика на shutdown
                    try:
                        await asyncio.sleep(1.0)
                    except asyncio.CancelledError:
                        raise  # Пробрасываем для правильного shutdown
            except asyncio.CancelledError:
                logger.info("Telegram supervisor cancelled - stopping polling")
                # КРИТИЧНО: Останавливаем updater при отмене supervisor
                if polling_task and not polling_task.done():
                    polling_task.cancel()
                    try:
                        await asyncio.wait_for(polling_task, timeout=2.0)
                    except (asyncio.CancelledError, asyncio.TimeoutError):
                        logger.debug("Polling task cancel: CancelledError or timeout (expected)")
                    except Exception as e:
                        logger.debug("Error waiting for polling task cancellation: %s: %s", type(e).__name__, e)

                try:
                    if app.updater and app.updater.running:
                        await asyncio.wait_for(app.updater.stop(), timeout=2.0)
                except Exception as e:
                    logger.debug("Error stopping updater during supervisor cancellation: %s: %s", type(e).__name__, e)
                raise  # Пробрасываем CancelledError

        except asyncio.CancelledError:
            # КРИТИЧНО: Обрабатываем CancelledError явно
            logger.info("Telegram supervisor cancelled - cleaning up")
            # Останавливаем updater при отмене
            if app and app.updater and app.updater.running:
                try:
                    await asyncio.wait_for(app.updater.stop(), timeout=2.0)
                except Exception as e:
                    logger.debug("Error stopping updater during cancellation: %s: %s", type(e).__name__, e)
            # Останавливаем application
            if app:
                try:
                    if hasattr(app, 'stop') and app.running:
                        await asyncio.wait_for(app.stop(), timeout=2.0)
                    if hasattr(app, 'shutdown'):
                        await asyncio.wait_for(app.shutdown(), timeout=2.0)
                except Exception as e:
                    logger.debug("Error shutting down app during cancellation: %s: %s", type(e).__name__, e)
            raise  # Пробрасываем CancelledError для правильного завершения
        except (NetworkError, Conflict) as e:
            logger.warning("TELEGRAM_NETWORK_FAILURE: %s: %s", type(e).__name__, e)
            # Exponential backoff
            backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            logger.info("Retrying in %.1fs...", backoff_seconds)

            # Sleep with shutdown check
            remaining = backoff_seconds
            while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем CancelledError
                remaining -= 1.0

        except Exception as e:
            logger.error("TELEGRAM_SUPERVISOR_ERROR: %s: %s", type(e).__name__, e)
            # Record error but continue
            get_state().record_error(f"TELEGRAM_SUPERVISOR: {type(e).__name__}")

            # Exponential backoff
            backoff_seconds = min(backoff_seconds * BACKOFF_MULTIPLIER, MAX_BACKOFF)
            logger.info("Retrying in %.1fs...", backoff_seconds)

            # Sleep with shutdown check
            remaining = backoff_seconds
            while remaining > 0 and not shutdown_evt.is_set() and get_state().system_health.is_running:
                try:
                    await asyncio.sleep(min(1.0, remaining))
                except asyncio.CancelledError:
                    raise  # Пробрасываем CancelledError
                remaining -= 1.0
        finally:
            # ========== REQUIREMENT 5: GRACEFUL SHUTDOWN (TELEGRAM) ==========
            # Cleanup polling task ПЕРВЫМ (если еще не остановлен)
            if polling_task is not None and not polling_task.done():
                try:
                    polling_task.cancel()
                    try:
                        await asyncio.wait_for(polling_task, timeout=2.0)
                    except asyncio.CancelledError:
                        pass  # Ожидаемое исключение при cancel
                    except asyncio.TimeoutError:
                        logger.warning("Telegram polling task did not cancel within timeout")
                    except Exception as e:
                        # Исключения во время shutdown (включая httpx.ReadError) не критичны
                        error_type = type(e).__name__
                        if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                            logger.debug("Telegram shutdown: Expected error during polling cancel: %s", error_type)
                        else:
                            logger.debug("Telegram shutdown: Error during polling cancel: %s: %s", error_type, e)
                except Exception as e:
                    logger.debug("Telegram shutdown: Error cancelling polling task: %s: %s", type(e).__name__, e)

            # Cleanup application ПОСЛЕ остановки polling
            if app is not None:
                try:
                    if app.updater and app.updater.running:
                        try:
                            await asyncio.wait_for(app.updater.stop(), timeout=2.0)
                        except Exception as e:
                            error_type = type(e).__name__
                            if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                                logger.debug("Telegram shutdown: Expected error during updater.stop(): %s", error_type)
                            else:
                                logger.debug("Telegram shutdown: Error during updater.stop(): %s: %s", error_type, e)

                    if hasattr(app, 'stop') and app.running:
                        try:
                            await asyncio.wait_for(app.stop(), timeout=2.0)
                        except Exception as e:
                            error_type = type(e).__name__
                            if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                                logger.debug("Telegram shutdown: Expected error during app.stop(): %s", error_type)
                            else:
                                logger.debug("Telegram shutdown: Error during app.stop(): %s: %s", error_type, e)

                    if hasattr(app, 'shutdown'):
                        try:
                            await asyncio.wait_for(app.shutdown(), timeout=2.0)
                        except Exception as e:
                            error_type = type(e).__name__
                            if "ReadError" in error_type or "httpx" in str(type(e)).lower():
                                logger.debug("Telegram shutdown: Expected error during app.shutdown(): %s", error_type)
                            else:
                                logger.debug("Telegram shutdown: Error during app.shutdown(): %s: %s", error_type, e)
                except Exception as e:
                    logger.debug("Telegram shutdown: Error during app cleanup: %s: %s", type(e).__name__, e)

    logger.info("📱 Telegram supervisor stopped")
