"""
HTTP-панель управления бота (control_plane/http.py): обработчики /admin/*,
/metrics, /admin/chaos/*, таблица маршрутов и сам сервер.

Тесты написаны на шаге 6а (пункт 5 плана отложенного, docs/DEFERRED_PLAN.md),
когда панель ещё жила в runner.py и у неё не было ни одного теста; на шаге 6в
она переехала, проверки поведения остались прежними. Они фиксируют связи через
общее состояние control_plane.state: ручную паузу (её читают цикл анализа и
метрики), счётчики команд и флаг хаоса, который пишут обработчики хаоса, а
читает runtime_heartbeat_loop (loops/runtime_heartbeat.py).

Обработчики вызываются напрямую: аргумент — состояние процесса, результат —
(код, тело); автомат состояний и движок хаоса подменены. Сервер проверяется
настоящими HTTP-запросами на свободный порт.
"""
import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

import runner
from control_plane import http as cp_http
from control_plane import state as cp_state
from loops import runtime_heartbeat


def call(handler, state):
    return asyncio.run(handler(state))


def process_state(trading_paused=False, safe_mode=False, consecutive_errors=0):
    return SimpleNamespace(system_health=SimpleNamespace(trading_paused=trading_paused, safe_mode=safe_mode,
                                                         consecutive_errors=consecutive_errors))


class FakeMachine:
    def __init__(self):
        self.synced = []
        self.transitions = []

    def sync_to_system_state(self, state, manual_pause_active):
        self.synced.append(manual_pause_active)

    async def transition_to(self, target, **kwargs):
        self.transitions.append((target, kwargs.get("owner")))


class FakeChaos:
    def __init__(self, inject_error=None, stopped=True):
        self.injected = []
        self._inject_error = inject_error
        self._stopped = stopped

    async def inject_chaos(self, chaos_type, duration):
        if self._inject_error:
            raise self._inject_error
        self.injected.append((chaos_type, duration))
        return "inc-test"

    async def stop_chaos(self):
        return self._stopped


@pytest.fixture
def plane(monkeypatch):
    """Чистое состояние панели: процесс, флаги, метрики, автомат, блокировка."""
    machine = FakeMachine()
    metrics = {
        "analysis_duration_buckets": {b: 0 for b in cp_state.ANALYSIS_DURATION_BUCKETS},
        "analysis_duration_sum": 0.0,
        "analysis_duration_count": 0,
        "scheduler_stalls_total": 0,
        "analysis_cycles_total": 0,
        "admin_commands_total": {"pause": {"success": 0}, "resume": {"success": 0, "blocked_safe_mode": 0}},
    }
    monkeypatch.setattr(cp_state, "control_plane_state", {"manual_pause_active": False})
    monkeypatch.setattr(cp_state, "prometheus_metrics", metrics)
    # Как сразу после запуска: цикл анализа ещё не выставил адаптивный интервал.
    monkeypatch.setattr(cp_state, "adaptive_system_state",
                        {"volatility_state": "MEDIUM", "adaptive_interval": None, "recovery_cycles": 0})
    monkeypatch.setattr(cp_http, "get_state_machine", lambda: machine)
    cp_state.reset_admin_lock()  # asyncio.Lock — на цикл событий теста
    monkeypatch.setitem(cp_state.chaos, "was_active", False)
    monkeypatch.delenv("CHAOS_ENABLED", raising=False)
    return SimpleNamespace(state=process_state(), machine=machine, metrics=metrics)


# ---------------------------------------------------------------------------
# Статус, пауза, возобновление
# ---------------------------------------------------------------------------

def test_status_reports_pause_and_safe_mode(plane):
    plane.state.system_health.safe_mode = True
    cp_state.control_plane_state["manual_pause_active"] = True
    status, body = call(cp_http.handle_admin_status, plane.state)
    data = json.loads(body)
    assert status == 200
    assert data["manual_pause_active"] is True and data["safe_mode"] is True
    assert data["trading_paused"] is False and "uptime_seconds" in data


def test_pause_sets_the_manual_flag_syncs_the_state_machine_and_counts(plane):
    assert call(cp_http.handle_admin_pause, plane.state) == (200, b'{"status": "paused"}')
    assert call(cp_http.handle_admin_pause, plane.state)[0] == 200, "пауза идемпотентна"
    assert cp_state.control_plane_state["manual_pause_active"] is True
    assert plane.machine.synced == [True, True]
    assert plane.metrics["admin_commands_total"]["pause"]["success"] == 2
    assert plane.state.system_health.safe_mode is False, "панель safe_mode не трогает"


def test_resume_is_refused_in_safe_mode_and_changes_nothing(plane):
    plane.state.system_health.safe_mode = True
    cp_state.control_plane_state["manual_pause_active"] = True
    status, body = call(cp_http.handle_admin_resume, plane.state)
    assert status == 403 and json.loads(body) == {"reason": "safe_mode_active"}
    assert cp_state.control_plane_state["manual_pause_active"] is True
    assert plane.machine.synced == []
    assert plane.metrics["admin_commands_total"]["resume"] == {"success": 0, "blocked_safe_mode": 1}


def test_resume_clears_the_manual_pause(plane):
    cp_state.control_plane_state["manual_pause_active"] = True
    assert call(cp_http.handle_admin_resume, plane.state) == (200, b'{"status": "resumed"}')
    assert cp_state.control_plane_state["manual_pause_active"] is False
    assert plane.machine.synced == [False]
    assert plane.metrics["admin_commands_total"]["resume"]["success"] == 1


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("safe_mode, errors, mode", [(False, 0, "NORMAL"), (True, 0, "SAFE_MODE"), (False, 2, "CAUTION")])
def test_metrics_label_the_mode(plane, safe_mode, errors, mode):
    plane.state.system_health.safe_mode = safe_mode
    plane.state.system_health.consecutive_errors = errors
    status, body = call(cp_http.handle_metrics, plane.state)
    text = body.decode()
    assert status == 200
    assert f'analysis_cycles_total{{mode="{mode}"}} 0' in text
    assert f"safe_mode {1 if safe_mode else 0}" in text


def test_metrics_answer_before_the_first_analysis_cycle(plane):
    """
    Найдено этими тестами 11.09.2026: пока цикл анализа не выставил адаптивный
    интервал (он None), /metrics падал с TypeError — HTTP 500 с запуска
    HTTP-сервера до старта цикла. Теперь в метрике базовый интервал.
    """
    status, body = call(cp_http.handle_metrics, plane.state)
    assert status == 200
    assert f"adaptive_analysis_interval_seconds {float(cp_http.settings.analysis_interval):.1f}" in body.decode()


def test_metrics_expose_the_control_plane_counters(plane):
    call(cp_http.handle_admin_pause, plane.state)
    plane.state.system_health.safe_mode = True
    call(cp_http.handle_admin_resume, plane.state)
    text = call(cp_http.handle_metrics, plane.state)[1].decode()
    assert "manual_pause_active 1" in text
    assert 'admin_commands_total{command="pause", result="success"} 1' in text
    assert 'admin_commands_total{command="resume", result="blocked_safe_mode"} 1' in text


def test_analysis_duration_fills_cumulative_buckets(monkeypatch):
    metrics = {"analysis_duration_buckets": {b: 0 for b in cp_state.ANALYSIS_DURATION_BUCKETS},
               "analysis_duration_sum": 0.0, "analysis_duration_count": 0}
    monkeypatch.setattr(cp_state, "prometheus_metrics", metrics)
    cp_state.record_analysis_duration(2.5)
    assert metrics["analysis_duration_buckets"] == {0.5: 0, 1.0: 0, 2.0: 0, 3.0: 1, 5.0: 1, 8.0: 1, 13.0: 1}
    assert metrics["analysis_duration_sum"] == 2.5 and metrics["analysis_duration_count"] == 1


@pytest.mark.parametrize("level, expected", [("LOW", "LOW"), ("NORMAL", "MEDIUM"), ("MEDIUM", "MEDIUM"),
                                             ("HIGH", "HIGH"), ("EXTREME", "HIGH"), ("BOGUS", "unchanged")])
def test_volatility_levels_collapse_to_three(monkeypatch, level, expected):
    monkeypatch.setattr(cp_state, "adaptive_system_state", {"volatility_state": "unchanged"})
    cp_state.update_volatility_state(level)
    assert cp_state.adaptive_system_state["volatility_state"] == expected


# ---------------------------------------------------------------------------
# Хаос
# ---------------------------------------------------------------------------

def test_chaos_is_off_by_default(plane):
    routes = cp_http.build_http_routes()
    assert set(routes) == {("GET", "/metrics"), ("GET", "/admin/status"),
                           ("POST", "/admin/pause"), ("POST", "/admin/resume")}
    assert call(cp_http.handle_chaos_inject, plane.state)[0] == 403
    assert call(cp_http.handle_chaos_stop, plane.state)[0] == 403


def test_chaos_injection_sets_the_flag_runtime_heartbeat_reads(plane, monkeypatch):
    """Флаг пишет обработчик, читает runtime_heartbeat_loop (loops/runtime_heartbeat.py) — связь, которую перенос обязан сохранить."""
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    chaos = FakeChaos()
    monkeypatch.setattr(cp_http, "get_chaos_engine", lambda: chaos)
    monkeypatch.setattr(cp_http, "log_task_dump", lambda *args, **kwargs: None)
    assert ("POST", "/admin/chaos/inject") in cp_http.build_http_routes()
    status, body = call(cp_http.handle_chaos_inject, plane.state)
    assert status == 200 and json.loads(body)["incident_id"] == "inc-test"
    assert cp_state.chaos["was_active"] is True
    assert 'cp_state.chaos["was_active"]' in inspect.getsource(runtime_heartbeat.runtime_heartbeat_loop)


def test_second_chaos_injection_is_a_conflict(plane, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setattr(cp_http, "get_chaos_engine", lambda: FakeChaos(inject_error=RuntimeError("already active")))
    assert call(cp_http.handle_chaos_inject, plane.state)[0] == 409
    assert cp_state.chaos["was_active"] is False


def test_stopping_chaos_outside_safe_mode_enforces_it_and_resets_the_flag(plane, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setattr(cp_http, "get_chaos_engine", lambda: FakeChaos(stopped=True))
    cp_state.chaos["was_active"] = True
    assert call(cp_http.handle_chaos_stop, plane.state)[0] == 200
    assert plane.machine.transitions == [(cp_http.SystemStateEnum.SAFE_MODE, "handle_chaos_stop")]
    assert cp_state.chaos["was_active"] is False


def test_stopping_without_active_chaos_is_404(plane, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setattr(cp_http, "get_chaos_engine", lambda: FakeChaos(stopped=False))
    assert call(cp_http.handle_chaos_stop, plane.state)[0] == 404
    assert plane.machine.transitions == []


# ---------------------------------------------------------------------------
# Сервер: настоящие HTTP-запросы на свободный порт
# ---------------------------------------------------------------------------

STATUS = b"GET /admin/status HTTP/1.1\r\nHost: x\r\n\r\n"


@pytest.fixture
def server_slot(monkeypatch):
    """Сервер — одиночка на процесс; каждому тесту свой слот."""
    monkeypatch.setattr(cp_http, "_http_server_started", False)
    monkeypatch.setattr(cp_http, "_http_server_instance", None)


async def _request(port, raw):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    await writer.drain()
    data = await reader.read()
    writer.close()
    head, _, body = data.partition(b"\r\n\r\n")
    lines = head.decode().split("\r\n")
    headers = dict(line.split(": ", 1) for line in lines[1:])
    return int(lines[0].split()[1]), headers, body


def serve(get_state, *requests, shutting_down=False):
    async def run():
        shutdown_evt = asyncio.Event()
        if shutting_down:
            shutdown_evt.set()
        server = await cp_http.start_http_server(get_state, shutdown_evt, port=0)
        port = server.sockets[0].getsockname()[1]
        try:
            return [await _request(port, raw) for raw in requests]
        finally:
            server.close()
            await server.wait_closed()
    return asyncio.run(run())


def test_server_routes_requests_to_the_handlers(plane, server_slot):
    plane.state.system_health.safe_mode = True
    (status, headers, body), (m_status, m_headers, m_body) = serve(
        lambda: plane.state, STATUS, b"GET /metrics/?x=1 HTTP/1.1\r\n\r\n")
    assert status == 200 and json.loads(body)["safe_mode"] is True
    assert headers["Content-Type"] == "application/json"
    assert int(headers["Content-Length"]) == len(body)
    assert m_status == 200, "хвостовой слэш и query не мешают маршруту"
    assert m_headers["Content-Type"] == "text/plain; version=0.0.4"
    assert b"safe_mode 1" in m_body


def test_server_reads_the_process_state_on_every_request(plane, server_slot):
    """runner может заменить system_state — сервер обязан брать текущий, а не запомненный при старте."""
    states = iter([process_state(), process_state(trading_paused=True, safe_mode=True)])
    first, second = serve(lambda: next(states), STATUS, STATUS)
    assert json.loads(first[2])["safe_mode"] is False
    assert json.loads(second[2])["safe_mode"] is True


def test_server_answers_405_404_and_400(plane, server_slot):
    results = serve(lambda: plane.state, b"GET /admin/pause HTTP/1.1\r\n\r\n",
                    b"GET /nope HTTP/1.1\r\n\r\n", b"GARBAGE\r\n\r\n")
    assert [r[0] for r in results] == [405, 404, 400]
    assert plane.machine.synced == [], "GET на /admin/pause паузу не ставит"


def test_server_refuses_requests_once_shutdown_began(plane, server_slot):
    """
    Исправлено на 6в: заголовок обещал 19 байт при теле в 21 — клиент получал
    обрезанный ответ; а на Linux ответ не доходил вовсе — сервер закрывал сокет,
    не прочитав запрос, и ядро слало RST (в CI — ConnectionResetError).
    """
    (status, headers, body), = serve(lambda: plane.state, b"POST /admin/pause HTTP/1.1\r\n\r\n",
                                     shutting_down=True)
    assert status == 503 and body == b"Service Shutting Down"
    assert int(headers["Content-Length"]) == len(body)
    assert plane.machine.synced == []


def test_handler_crash_is_a_500_not_a_dropped_connection(plane, server_slot, monkeypatch):
    def broken_machine():
        raise RuntimeError("state machine down")
    monkeypatch.setattr(cp_http, "get_state_machine", broken_machine)
    (status, _, body), = serve(lambda: plane.state, b"POST /admin/pause HTTP/1.1\r\n\r\n")
    assert status == 500 and json.loads(body)["status"] == "error"


def test_second_start_returns_the_running_server(plane, server_slot):
    async def run():
        first = await cp_http.start_http_server(lambda: plane.state, asyncio.Event(), port=0)
        try:
            assert await cp_http.start_http_server(lambda: plane.state, asyncio.Event(), port=0) is first
            assert cp_http.current_server() is first
        finally:
            first.close()
            await first.wait_closed()
        cp_http.forget_server()
        assert cp_http.current_server() is None
    asyncio.run(run())


# ---------------------------------------------------------------------------
# Связка с runner
# ---------------------------------------------------------------------------

def test_runner_hands_the_panel_its_settings_and_live_state():
    assert cp_http.settings.analysis_interval == runner.ANALYSIS_INTERVAL
    assert cp_http.settings.auto_resume_enabled == runner.AUTO_RESUME_TRADING_ENABLED
    assert cp_http.settings.auto_resume_success_cycles == runner.AUTO_RESUME_SUCCESS_CYCLES
    source = inspect.getsource(runner)
    assert "cp_http.configure(analysis_interval=ANALYSIS_INTERVAL," in source
    assert "cp_http.start_http_server(_state, get_shutdown_event())" in inspect.getsource(runner.main)
    assert "cp_http.forget_server()" in inspect.getsource(runner.main)
    for name in ("handle_admin_status", "build_http_routes", "start_http_server", "_http_server_instance"):
        assert not hasattr(runner, name), f"{name} — только в control_plane/http.py"


def test_runner_shares_the_very_same_state_objects():
    """Шаги 6б–6в: runner держит ссылки на объекты и функции control_plane.state, а не копии."""
    assert runner._analysis_metrics is cp_state.analysis_metrics
    assert runner._prometheus_metrics is cp_state.prometheus_metrics
    assert runner._adaptive_system_state is cp_state.adaptive_system_state
    assert runner._control_plane_state is cp_state.control_plane_state
    assert runner._metrics_lock is cp_state.metrics_lock
    assert runner._get_admin_lock is cp_state.get_admin_lock
    assert runner.ANALYSIS_DURATION_BUCKETS is cp_state.ANALYSIS_DURATION_BUCKETS
    for name in ("get_analysis_metrics", "update_analysis_metrics", "get_prometheus_metrics",
                 "record_analysis_duration", "increment_scheduler_stalls", "increment_analysis_cycles",
                 "get_adaptive_system_state", "update_volatility_state"):
        assert getattr(runner, name) is getattr(cp_state, name), name
