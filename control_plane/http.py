"""
HTTP-панель управления процессом бота: /admin/status, /admin/pause,
/admin/resume, /metrics и — при CHAOS_ENABLED=true — /admin/chaos/*.
Слушает только 127.0.0.1:8080 внутри контейнера.

Вынесена из runner.py без изменения логики (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 6в). Общее состояние (ручная пауза, счётчики,
флаг хаоса) — в control_plane.state. Состояние процесса обработчик получает
аргументом: сервер берёт его функцией get_state на каждый запрос, как задачи
из loops/, — runner может заменить свой system_state. Настройки процесса
runner передаёт один раз через configure().
"""
import asyncio
import json
import logging
import os
import time
from types import SimpleNamespace

from chaos_engine import ChaosType, get_chaos_engine
from control_plane import state as cp_state
from system_state_machine import SystemState as SystemStateEnum, get_state_machine
from task_dump import log_task_dump

logger = logging.getLogger(__name__)

# Значения по умолчанию совпадают с runner; runner передаёт свои через configure().
settings = SimpleNamespace(analysis_interval=300, auto_resume_enabled=True, auto_resume_success_cycles=3)


def configure(*, analysis_interval, auto_resume_enabled, auto_resume_success_cycles):
    """Настройки процесса, которые показывает /metrics (их читает из окружения runner)."""
    settings.analysis_interval = analysis_interval
    settings.auto_resume_enabled = auto_resume_enabled
    settings.auto_resume_success_cycles = auto_resume_success_cycles


async def handle_admin_status(state):
    """
    GET /admin/status - возвращает статус системы
    
    SAFE MODE HARD LOCK:
    - safe_mode является read-only (только чтение)
    - Этот endpoint НЕ изменяет safe_mode
    """
    # GLOBAL STATE (intentional) - только чтение
    metrics = cp_state.get_analysis_metrics()
    uptime = 0.0
    if metrics.get("start_time") is not None:
        uptime = time.monotonic() - metrics["start_time"]
    
    # SAFE MODE HARD LOCK: safe_mode только читается, никогда не изменяется
    status_data = {
        "trading_paused": state.system_health.trading_paused,
        "manual_pause_active": cp_state.control_plane_state.get("manual_pause_active", False),
        "safe_mode": state.system_health.safe_mode,  # READ-ONLY
        "uptime_seconds": round(uptime, 2)
    }
    return 200, json.dumps(status_data, indent=2).encode('utf-8')

async def handle_admin_pause(state):
    """
    POST /admin/pause - приостанавливает торговлю
    
    SAFE MODE HARD LOCK:
    - safe_mode является read-only для HTTP API
    - safe_mode НЕ изменяется через этот endpoint
    - Если safe_mode == True, trading_paused уже должен быть True
    """
    # GLOBAL STATE (intentional)
    logger.info("ADMIN COMMAND RECEIVED: pause")
    
    # REQUIREMENT: Concurrency safety - prevent race conditions
    async with cp_state.get_admin_lock():
        try:
            # Idempotent: можно вызывать несколько раз
            # Атомарное обновление состояния
            # HARDENING: safe_mode НЕ изменяется здесь - он остается как есть
            cp_state.control_plane_state["manual_pause_active"] = True
            # HARDENING: Синхронизируем trading_paused через state machine
            state_machine = get_state_machine()
            state_machine.sync_to_system_state(state, manual_pause_active=True)
            # SAFE MODE HARD LOCK: safe_mode остается неизменным
            
            # Инкрементируем метрику ДО возврата ответа (новая структура с result labels)
            if "success" not in cp_state.prometheus_metrics["admin_commands_total"]["pause"]:
                cp_state.prometheus_metrics["admin_commands_total"]["pause"]["success"] = 0
            cp_state.prometheus_metrics["admin_commands_total"]["pause"]["success"] += 1
            
            logger.info("ADMIN COMMAND APPLIED: pause - trading_paused=True, manual_pause_active=True")
            return 200, json.dumps({"status": "paused"}).encode('utf-8')
        except Exception as e:
            logger.error("ADMIN COMMAND ERROR: pause - %s: %s", type(e).__name__, e)
            raise

async def handle_admin_resume(state):
    """
    POST /admin/resume - возобновляет торговлю
    
    SAFE MODE HARD LOCK:
    - Если safe_mode == True, команда БЛОКИРУЕТСЯ с HTTP 403
    - safe_mode НИКОГДА не изменяется через HTTP
    - Метрики отражают реальный результат (blocked_safe_mode или success)
    """
    # GLOBAL STATE (intentional)
    logger.info("ADMIN COMMAND RECEIVED: resume")
    
    # REQUIREMENT: Concurrency safety - prevent race conditions
    # WHY: Concurrent HTTP requests cannot race-clear safe_mode or resume trading while safe_mode == true
    async with cp_state.get_admin_lock():
        # ========== SAFE MODE HARD LOCK ENFORCEMENT ==========
        # КРИТИЧНО: safe_mode является read-only для HTTP API
        # Проверяем safe_mode ПЕРЕД любыми изменениями состояния (hard lock)
        safe_mode_before = state.system_health.safe_mode
        
        if safe_mode_before:
            # SAFE MODE HARD LOCK: блокируем команду
            # Инкрементируем метрику для blocked_safe_mode
            if "blocked_safe_mode" not in cp_state.prometheus_metrics["admin_commands_total"]["resume"]:
                cp_state.prometheus_metrics["admin_commands_total"]["resume"]["blocked_safe_mode"] = 0
            cp_state.prometheus_metrics["admin_commands_total"]["resume"]["blocked_safe_mode"] += 1
            
            # ВАЖНО: trading_paused и manual_pause_active НЕ изменяются
            trading_paused_before = state.system_health.trading_paused
            manual_pause_before = cp_state.control_plane_state["manual_pause_active"]
            
            # WARN-level logging as required: "ADMIN RESUME BLOCKED: safe_mode_active"
            logger.warning(
                "ADMIN RESUME BLOCKED: safe_mode_active. State preserved: trading_paused=%s, manual_pause_active=%s. Safe mode can only be cleared by recovery cycles or process restart.",
                trading_paused_before, manual_pause_before
            )
            
            # Возвращаем HTTP 403 с правильным JSON форматом
            # REQUIREMENT: response body MUST include reason: "safe_mode_active"
            return 403, json.dumps({
                "reason": "safe_mode_active"
            }).encode('utf-8')
        
        # ========== SAFE MODE CHECK PASSED - PROCEED WITH RESUME ==========
        # Атомарное обновление состояния
        # HARDENING: safe_mode НЕ изменяется здесь - он остается как есть
        cp_state.control_plane_state["manual_pause_active"] = False
        # HARDENING: Синхронизируем trading_paused через state machine
        state_machine = get_state_machine()
        state_machine.sync_to_system_state(state, manual_pause_active=False)
        
        # Инкрементируем метрику для успешного выполнения
        if "success" not in cp_state.prometheus_metrics["admin_commands_total"]["resume"]:
            cp_state.prometheus_metrics["admin_commands_total"]["resume"]["success"] = 0
        cp_state.prometheus_metrics["admin_commands_total"]["resume"]["success"] += 1
        
        # Логируем подтверждение, что safe_mode не изменен
        safe_mode_after = state.system_health.safe_mode
        logger.info(
            "ADMIN COMMAND APPLIED: resume - trading_paused=False, manual_pause_active=False. SAFE MODE HARD LOCK verified: safe_mode=%s (unchanged from %s)",
            safe_mode_after, safe_mode_before
        )
        return 200, json.dumps({"status": "resumed"}).encode('utf-8')

async def handle_metrics(state):
    """GET /metrics - возвращает Prometheus-совместимые метрики"""
    # GLOBAL STATE (intentional) - только чтение
    metrics = cp_state.get_analysis_metrics()
    prom_metrics = cp_state.get_prometheus_metrics()
    
    # Вычисляем uptime
    uptime = 0.0
    if metrics.get("start_time") is not None:
        uptime = time.monotonic() - metrics["start_time"]
    
    # Определяем mode для labels (low cardinality)
    mode = "SAFE_MODE" if state.system_health.safe_mode else "NORMAL"
    if state.system_health.consecutive_errors > 0:
        mode = "CAUTION"
    
    # Формируем Prometheus metrics
    lines = []
    
    # Histogram: market_analysis_duration_seconds
    for bucket in cp_state.ANALYSIS_DURATION_BUCKETS:
        count = prom_metrics["analysis_duration_buckets"].get(bucket, 0)
        lines.append(f'market_analysis_duration_seconds_bucket{{le="{bucket:.1f}",mode="{mode}"}} {count}')
    total_count = prom_metrics["analysis_duration_count"]
    lines.append(f'market_analysis_duration_seconds_bucket{{le="+Inf",mode="{mode}"}} {total_count}')
    lines.append(f'market_analysis_duration_seconds_sum{{mode="{mode}"}} {prom_metrics["analysis_duration_sum"]:.3f}')
    lines.append(f'market_analysis_duration_seconds_count{{mode="{mode}"}} {total_count}')
    
    # Gauge: last_analysis_duration_seconds
    duration = metrics.get("last_analysis_duration", 0.0)
    lines.append(f'last_analysis_duration_seconds{{mode="{mode}"}} {duration:.3f}')
    
    # Counters
    runs_total = metrics.get("analysis_count", 0)
    lines.append(f'market_analysis_runs_total {runs_total}')
    cycles_total = prom_metrics["analysis_cycles_total"]
    lines.append(f'analysis_cycles_total{{mode="{mode}"}} {cycles_total}')
    errors_total = state.system_health.consecutive_errors
    lines.append(f'market_analysis_errors_total{{mode="{mode}"}} {errors_total}')
    stalls_total = prom_metrics["scheduler_stalls_total"]
    lines.append(f'scheduler_stalls_total {stalls_total}')
    
    # Gauges
    lines.append(f'market_volatility 0.000')
    lines.append(f'uptime_seconds {uptime:.3f}')
    safe_mode_value = 1 if state.system_health.safe_mode else 0
    lines.append(f'safe_mode {safe_mode_value}')
    trading_paused_value = 1 if state.system_health.trading_paused else 0
    lines.append(f'trading_paused {trading_paused_value}')
    
    # Adaptive system metrics
    adaptive_system = cp_state.get_adaptive_system_state()
    # До первого прохода цикла анализа ключ есть, но хранит None, и .get(..., default)
    # возвращал None: /metrics падал с TypeError (500) с запуска HTTP-сервера до старта
    # цикла. Найдено тестами панели 11.09.2026.
    adaptive_interval = adaptive_system.get("adaptive_interval") or float(settings.analysis_interval)
    lines.append(f'adaptive_analysis_interval_seconds {adaptive_interval:.1f}')
    recovery_cycles = adaptive_system.get("recovery_cycles", 0)
    recovery_remaining = max(0, settings.auto_resume_success_cycles - recovery_cycles) if settings.auto_resume_enabled else 0
    lines.append(f'recovery_cycles_remaining {recovery_remaining}')
    
    # Control plane metrics
    manual_pause_value = 1 if cp_state.control_plane_state["manual_pause_active"] else 0
    lines.append(f'manual_pause_active {manual_pause_value}')
    
    # Admin commands metrics with result labels
    admin_commands = cp_state.prometheus_metrics["admin_commands_total"]
    # Pause commands
    pause_success = admin_commands.get("pause", {}).get("success", 0)
    lines.append(f'admin_commands_total{{command="pause", result="success"}} {pause_success}')
    # Resume commands
    resume_success = admin_commands.get("resume", {}).get("success", 0)
    resume_blocked = admin_commands.get("resume", {}).get("blocked_safe_mode", 0)
    lines.append(f'admin_commands_total{{command="resume", result="success"}} {resume_success}')
    lines.append(f'admin_commands_total{{command="resume", result="blocked_safe_mode"}} {resume_blocked}')
    
    # Объединяем все метрики
    body = '\n'.join(lines) + '\n'
    return 200, body.encode('utf-8')

async def handle_chaos_inject(state):
    """
    POST /admin/chaos/inject - Инъекция chaos для тестирования recovery
    
    ТРЕБОВАНИЯ:
    - Только в debug/admin mode (проверка через env или auth)
    - Гарантированно вызывает event loop stall
    - Воспроизводится 100% по команде
    
    Body: {"type": "cross_lock_deadlock|sync_io_block|recursive_await|cpu_bound_loop", "duration": 300}
    """
    # GLOBAL STATE (intentional)
    # Проверка доступа (только в debug mode)
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if not chaos_enabled:
        return 403, json.dumps({
            "error": "chaos_disabled",
            "message": "Chaos injection disabled. Set CHAOS_ENABLED=true to enable."
        }).encode('utf-8')
    
    try:
        # Парсим body
        # В реальности нужно прочитать body из request
        # Для упрощения используем query params или defaults
        chaos_type_str = "cpu_bound_loop"  # Default
        duration = 300.0  # Default
        
        # Определяем тип chaos
        chaos_type_map = {
            "cross_lock_deadlock": ChaosType.CROSS_LOCK_DEADLOCK,
            "sync_io_block": ChaosType.SYNC_IO_BLOCK,
            "recursive_await": ChaosType.RECURSIVE_AWAIT,
            "cpu_bound_loop": ChaosType.CPU_BOUND_LOOP,
        }
        
        chaos_type = chaos_type_map.get(chaos_type_str, ChaosType.CPU_BOUND_LOOP)
        
        # Инъекция chaos
        chaos_engine = get_chaos_engine()
        incident_id = await chaos_engine.inject_chaos(chaos_type, duration)
        
        # ========== REQUIREMENT 2: CHAOS INVARIANT ==========
        # Если chaos был активен И произошёл heartbeat miss:
        # система ОБЯЗАНА пройти через SAFE_MODE
        cp_state.chaos["was_active"] = True
        logger.critical(
            "CHAOS_INJECTION_TRIGGERED (invariant tracking enabled) incident_id=%s chaos_type=%s duration=%ss",
            incident_id, chaos_type.value, duration
        )
        
        # Task dump перед инъекцией
        try:
            log_task_dump(incident_id, context="CHAOS_INJECTION_START")
        except Exception:
            # Не критично, но след в логе нужен.
            logger.debug("task_dump перед инъекцией хаоса недоступен", exc_info=True)
        
        return 200, json.dumps({
            "status": "chaos_injected",
            "incident_id": incident_id,
            "chaos_type": chaos_type.value,
            "duration": duration,
            "message": f"Chaos injection started. Event loop will stall for {duration}s."
        }).encode('utf-8')
        
    except RuntimeError as e:
        # Chaos уже активен
        return 409, json.dumps({
            "error": "chaos_already_active",
            "message": str(e)
        }).encode('utf-8')
    except Exception as e:
        logger.error("CHAOS_INJECTION_ERROR: %s: %s", type(e).__name__, e)
        return 500, json.dumps({
            "error": "chaos_injection_failed",
            "message": str(e)
        }).encode('utf-8')

async def handle_chaos_stop(state):
    """
    POST /admin/chaos/stop - Остановка активной chaos-инъекции
    
    REQUIREMENT 2: После остановки chaos проверяем инвариант:
    - Если chaos был активен И произошёл heartbeat miss → SAFE_MODE обязателен
    """
    # GLOBAL STATE (intentional)
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if not chaos_enabled:
        return 403, json.dumps({
            "error": "chaos_disabled"
        }).encode('utf-8')
    
    try:
        chaos_engine = get_chaos_engine()
        stopped = await chaos_engine.stop_chaos()
        
        # ========== REQUIREMENT 2: CHAOS INVARIANT ENFORCEMENT ==========
        # Если chaos был активен, проверяем что система прошла через SAFE_MODE
        if cp_state.chaos["was_active"] and stopped:
            if not state.system_health.safe_mode:
                # ИНВАРИАНТ НАРУШЕН: chaos был активен, но система не в SAFE_MODE
                # Принудительно активируем SAFE_MODE
                import uuid
                incident_id = f"chaos-invariant-{uuid.uuid4().hex[:8]}"
                # HARDENING: SAFE_MODE activation for chaos invariant через state machine
                state_machine = get_state_machine()
                await state_machine.transition_to(
                    SystemStateEnum.SAFE_MODE,
                    reason="CHAOS_INVARIANT_ENFORCEMENT: chaos was active but system not in SAFE_MODE",
                    owner="handle_chaos_stop",
                    metadata={"incident_id": incident_id}
                )
                logger.critical(
                    "CHAOS_INVARIANT_ENFORCEMENT: SAFE_MODE activated - chaos was active but system not in SAFE_MODE incident_id=%s",
                    incident_id
                )
            
            # Сбрасываем флаг после проверки инварианта
            cp_state.chaos["was_active"] = False
        
        if stopped:
            return 200, json.dumps({
                "status": "chaos_stopped",
                "message": "Chaos injection stopped successfully"
            }).encode('utf-8')
        else:
            return 404, json.dumps({
                "error": "no_active_chaos",
                "message": "No active chaos injection"
            }).encode('utf-8')
    except Exception as e:
        logger.error("CHAOS_STOP_ERROR: %s: %s", type(e).__name__, e)
        return 500, json.dumps({
            "error": "chaos_stop_failed",
            "message": str(e)
        }).encode('utf-8')


def build_http_routes():
    """
    ЕДИНАЯ таблица маршрутов для HTTP сервера
    
    ВСЕ routes регистрируются здесь - единый источник истины.
    """
    routes = {
        ("GET", "/metrics"): handle_metrics,
        ("GET", "/admin/status"): handle_admin_status,
        ("POST", "/admin/pause"): handle_admin_pause,
        ("POST", "/admin/resume"): handle_admin_resume,
    }
    
    # Chaos endpoints (только если включён)
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if chaos_enabled:
        routes[("POST", "/admin/chaos/inject")] = handle_chaos_inject
        routes[("POST", "/admin/chaos/stop")] = handle_chaos_stop
    
    return routes


# HTTP Server lifecycle state (singleton protection)
_http_server_started = False
_http_server_instance = None

async def start_http_server(get_state, shutdown_evt, host="127.0.0.1", port=8080):
    """
    HTTP сервер для health/metrics/admin endpoints.
    Production-safe HTTP/1.1 router с таблицей маршрутов.

    get_state — функция, отдающая текущий system_state: сервер зовёт её на
    каждый запрос и передаёт результат обработчику. shutdown_evt — событие
    остановки процесса. host/port — для тестов (свободный порт 0).
    
    Returns:
        asyncio.Server: Server object для graceful shutdown
    
    Safety:
    - Single-instance protection (prevents double startup)
    - Event loop ownership check
    - Graceful shutdown support
    """
    global _http_server_started, _http_server_instance
    
    # Защита от двойного старта
    if _http_server_started:
        logger.warning("HTTP SERVER: Attempted double startup, returning existing instance")
        return _http_server_instance
    
    # Проверка event loop ownership
    try:
        loop = asyncio.get_running_loop()
        loop_id = id(loop)
        logger.critical("HTTP SERVER STARTED (loop id=%s)", loop_id)
    except RuntimeError as e:
        logger.error("HTTP SERVER: No running event loop - %s: %s", type(e).__name__, e)
        raise
    
    # ========== ЕДИНЫЙ ROUTER ==========
    # Используем build_http_routes() - единый источник истины
    routes = build_http_routes()
    
    # Жёсткая проверка: chaos routes должны быть зарегистрированы если включён
    chaos_enabled = os.environ.get("CHAOS_ENABLED", "false").lower() == "true"
    if chaos_enabled:
        assert ("POST", "/admin/chaos/inject") in routes, \
            "CHAOS ROUTE NOT REGISTERED — CONTROL PLANE BROKEN"
        assert ("POST", "/admin/chaos/stop") in routes, \
            "CHAOS STOP ROUTE NOT REGISTERED — CONTROL PLANE BROKEN"
    
    # Логируем зарегистрированные routes
    route_list = [f"{method} {path}" for (method, path) in routes.keys()]
    logger.critical("HTTP ROUTES REGISTERED: %s", route_list)
    
    # ========== HTTP REQUEST DISPATCHER ==========
    
    async def http_dispatcher(reader, writer):
        """HTTP/1.1 request dispatcher - использует единую таблицу routes из замыкания"""
        # КРИТИЧНО: Проверяем shutdown event ПЕРЕД обработкой запроса
        # Это гарантирует, что после начала shutdown новые запросы не обрабатываются
        if shutdown_evt.is_set():
            # Shutdown начался - немедленно возвращаем 503 и закрываем соединение.
            # Запрос сперва коротко дочитываем: закрытие сокета с непрочитанными
            # данными на Linux шлёт RST вместо FIN, и клиент терял ответ 503
            # (ConnectionResetError). Найдено тестом в CI на шаге 6в.
            try:
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=1.0)
            except Exception:
                logger.debug("Shutdown: HTTP request not fully read before 503", exc_info=True)
            try:
                response = (
                    b"HTTP/1.1 503 Service Unavailable\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: 21\r\n"
                    b"Connection: close\r\n"
                    b"\r\n"
                    b"Service Shutting Down"
                )
                writer.write(response)
                await writer.drain()
            except Exception:
                logger.debug("Failed to send shutdown response to HTTP client", exc_info=True)
            finally:
                try:
                    if not writer.is_closing():
                        writer.close()
                except Exception:
                    logger.debug("Failed to close HTTP writer during shutdown", exc_info=True)
            return  # Немедленный выход - не обрабатываем запрос
        
        status_code = 500
        response_body = b"Internal Server Error"
        content_type = "application/json"
        
        try:
            # Безопасное чтение HTTP request (до \r\n\r\n)
            try:
                request_data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5.0)
            except asyncio.TimeoutError:
                status_code = 408
                response_body = b"Request Timeout"
                content_type = "text/plain"
                logger.warning("HTTP REQUEST: Timeout reading request")
            except asyncio.IncompleteReadError:
                status_code = 400
                response_body = b"Bad Request: Incomplete request"
                content_type = "text/plain"
                logger.warning("HTTP REQUEST: Incomplete request")
            else:
                # Парсим request line
                request_text = request_data.decode('utf-8', errors='ignore')
                lines = request_text.split('\r\n')
                if not lines or not lines[0]:
                    status_code = 400
                    response_body = b"Bad Request: Empty request line"
                    content_type = "text/plain"
                    logger.warning("HTTP REQUEST: Empty request line")
                else:
                    # Parse method and path
                    request_line = lines[0].strip()
                    parts = request_line.split()
                    if len(parts) < 2:
                        status_code = 400
                        response_body = b"Bad Request: Invalid request line"
                        content_type = "text/plain"
                        logger.warning("HTTP REQUEST: Invalid request line: %s", request_line)
                    else:
                        method = parts[0].strip().upper()
                        path_with_query = parts[1].strip()
                        path = path_with_query.split('?')[0].strip()
                        # Normalize path (remove trailing slash except root)
                        if path != '/' and path.endswith('/'):
                            path = path.rstrip('/')
                        
                        logger.info("HTTP REQUEST %s %s", method, path)
                        
                        # КРИТИЧНО: Повторная проверка shutdown event перед обработкой
                        # Это гарантирует, что даже если соединение установлено до shutdown,
                        # мы не обрабатываем запрос
                        if shutdown_evt.is_set():
                            status_code = 503
                            response_body = b"Service Shutting Down"
                            content_type = "text/plain"
                            logger.info("HTTP RESPONSE 503: Shutdown in progress - %s %s", method, path)
                        else:
                            # Route lookup - используем routes из замыкания
                            route_key = (method, path)
                            if route_key in routes:
                                # Вызываем handler
                                handler = routes[route_key]
                                try:
                                    # КРИТИЧНО: Проверяем shutdown event перед вызовом handler
                                    if shutdown_evt.is_set():
                                        status_code = 503
                                        response_body = b"Service Shutting Down"
                                        content_type = "text/plain"
                                        logger.info("HTTP RESPONSE 503: Shutdown during handler - %s %s", method, path)
                                    else:
                                        status_code, response_body = await handler(get_state())
                                    
                                    # Определяем content-type по path и status_code
                                    if path == "/metrics":
                                        content_type = "text/plain; version=0.0.4"
                                    elif status_code == 403:
                                        # 403 Forbidden должен возвращать JSON с reason
                                        content_type = "application/json"
                                    else:
                                        content_type = "application/json"
                                    logger.info("HTTP RESPONSE %d %s %s", status_code, method, path)
                                except Exception as e:
                                    status_code = 500
                                    response_body = json.dumps({"status": "error", "message": "Internal Server Error"}).encode('utf-8')
                                    content_type = "application/json"
                                    logger.error("HTTP RESPONSE 500: Handler error: %s: %s - %s %s", type(e).__name__, e, method, path)
                            else:
                                # Проверяем, есть ли path с другим method
                                path_exists = any(r[1] == path for r in routes.keys())
                                if path_exists:
                                    # Path существует, но method неверный → 405
                                    status_code = 405
                                    response_body = b"Method Not Allowed"
                                    content_type = "text/plain"
                                    logger.info("HTTP RESPONSE 405: %s %s", method, path)
                                else:
                                    # Path не существует → 404
                                    status_code = 404
                                    response_body = b"Not Found"
                                    content_type = "text/plain"
                                    logger.info("HTTP RESPONSE 404: %s %s", method, path)
            
            # Формируем HTTP response
            status_text = {
                200: "OK",
                400: "Bad Request",
                403: "Forbidden",
                404: "Not Found",
                405: "Method Not Allowed",
                408: "Request Timeout",
                500: "Internal Server Error"
            }.get(status_code, "Unknown")
            
            response_headers = (
                f"HTTP/1.1 {status_code} {status_text}\r\n"
                f"Content-Type: {content_type}\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            response = response_headers.encode('utf-8') + response_body
            
            # Отправляем ответ
            writer.write(response)
            await writer.drain()
            
        except Exception as e:
            # Критическая ошибка - отправляем 500
            try:
                error_body = json.dumps({"status": "error", "message": "Internal Server Error"}).encode('utf-8')
                response = (
                    f"HTTP/1.1 500 Internal Server Error\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(error_body)}\r\n"
                    f"Connection: close\r\n"
                    f"\r\n"
                ).encode('utf-8') + error_body
                writer.write(response)
                await writer.drain()
                logger.error("HTTP RESPONSE 500: Critical error: %s: %s", type(e).__name__, e)
            except Exception:
                logger.debug("Failed to send HTTP 500 error response", exc_info=True)
        finally:
            # Закрываем writer (БЕЗ await wait_closed для безопасности event loop)
            try:
                if not writer.is_closing():
                    writer.close()
            except Exception:
                logger.debug("Failed to close HTTP writer", exc_info=True)
    
    # УДАЛЕНО: Все handlers теперь на уровне модуля (выше)
    # УДАЛЕНО: Локальная таблица ROUTES - используем build_http_routes()
    
    logger.critical("Creating HTTP server on %s:%s", host, port)
    server = await asyncio.start_server(http_dispatcher, host, port)
    await server.start_serving()  # КРИТИЧНО: Явно стартуем сервер!
    
    # Сохраняем состояние singleton
    _http_server_started = True
    _http_server_instance = server
    
    logger.critical("HTTP SERVER LISTENING ON %s:%s", host, port)
    return server


def current_server():
    """Запущенный сервер или None — для остановки процесса в runner.main()."""
    return _http_server_instance


def forget_server():
    """Забыть сервер после остановки (раньше runner обнулял свою глобальную ссылку)."""
    global _http_server_instance
    _http_server_instance = None
