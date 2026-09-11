"""
HTTP-панель управления бота (runner.py: handle_admin_*, handle_metrics,
handle_chaos_*, build_http_routes) — поведение как есть, до переноса из runner
(пункт 5 плана отложенного, шаг 6а, docs/DEFERRED_PLAN.md).

До 11.09.2026 у панели не было ни одного теста. Здесь зафиксировано то, что
перенос обязан сохранить, — в первую очередь связи через общее состояние
runner: _control_plane_state (его читают цикл анализа и метрики),
_prometheus_metrics и глобальный флаг _chaos_was_active, который пишут
обработчики хаоса, а читает runtime_heartbeat_loop.

Обработчики вызываются напрямую (они без аргументов и возвращают
(код, тело)); состояние процесса, автомат состояний и движок хаоса подменены.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

import runner


def call(handler):
    return asyncio.run(handler())


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
    state = SimpleNamespace(system_health=SimpleNamespace(trading_paused=False, safe_mode=False,
                                                          consecutive_errors=0))
    machine = FakeMachine()
    metrics = {
        "analysis_duration_buckets": {b: 0 for b in runner.ANALYSIS_DURATION_BUCKETS},
        "analysis_duration_sum": 0.0,
        "analysis_duration_count": 0,
        "scheduler_stalls_total": 0,
        "analysis_cycles_total": 0,
        "admin_commands_total": {"pause": {"success": 0}, "resume": {"success": 0, "blocked_safe_mode": 0}},
    }
    monkeypatch.setattr(runner, "system_state", state)
    monkeypatch.setattr(runner, "_control_plane_state", {"manual_pause_active": False})
    monkeypatch.setattr(runner, "_prometheus_metrics", metrics)
    # Как сразу после запуска: цикл анализа ещё не выставил адаптивный интервал.
    monkeypatch.setattr(runner, "_adaptive_system_state",
                        {"volatility_state": "MEDIUM", "adaptive_interval": None, "recovery_cycles": 0})
    monkeypatch.setattr(runner, "get_state_machine", lambda: machine)
    monkeypatch.setattr(runner, "_admin_command_lock", None)  # asyncio.Lock — на цикл событий теста
    monkeypatch.setattr(runner, "_chaos_was_active", False)
    monkeypatch.delenv("CHAOS_ENABLED", raising=False)
    return SimpleNamespace(state=state, machine=machine, metrics=metrics)


# ---------------------------------------------------------------------------
# Статус, пауза, возобновление
# ---------------------------------------------------------------------------

def test_status_reports_pause_and_safe_mode(plane):
    plane.state.system_health.safe_mode = True
    runner._control_plane_state["manual_pause_active"] = True
    status, body = call(runner.handle_admin_status)
    data = json.loads(body)
    assert status == 200
    assert data["manual_pause_active"] is True and data["safe_mode"] is True
    assert data["trading_paused"] is False and "uptime_seconds" in data


def test_pause_sets_the_manual_flag_syncs_the_state_machine_and_counts(plane):
    assert call(runner.handle_admin_pause) == (200, b'{"status": "paused"}')
    assert call(runner.handle_admin_pause)[0] == 200, "пауза идемпотентна"
    assert runner._control_plane_state["manual_pause_active"] is True
    assert plane.machine.synced == [True, True]
    assert plane.metrics["admin_commands_total"]["pause"]["success"] == 2
    assert plane.state.system_health.safe_mode is False, "панель safe_mode не трогает"


def test_resume_is_refused_in_safe_mode_and_changes_nothing(plane):
    plane.state.system_health.safe_mode = True
    runner._control_plane_state["manual_pause_active"] = True
    status, body = call(runner.handle_admin_resume)
    assert status == 403 and json.loads(body) == {"reason": "safe_mode_active"}
    assert runner._control_plane_state["manual_pause_active"] is True
    assert plane.machine.synced == []
    assert plane.metrics["admin_commands_total"]["resume"] == {"success": 0, "blocked_safe_mode": 1}


def test_resume_clears_the_manual_pause(plane):
    runner._control_plane_state["manual_pause_active"] = True
    assert call(runner.handle_admin_resume) == (200, b'{"status": "resumed"}')
    assert runner._control_plane_state["manual_pause_active"] is False
    assert plane.machine.synced == [False]
    assert plane.metrics["admin_commands_total"]["resume"]["success"] == 1


# ---------------------------------------------------------------------------
# Метрики
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("safe_mode, errors, mode", [(False, 0, "NORMAL"), (True, 0, "SAFE_MODE"), (False, 2, "CAUTION")])
def test_metrics_label_the_mode(plane, safe_mode, errors, mode):
    plane.state.system_health.safe_mode = safe_mode
    plane.state.system_health.consecutive_errors = errors
    status, body = call(runner.handle_metrics)
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
    status, body = call(runner.handle_metrics)
    assert status == 200
    assert f"adaptive_analysis_interval_seconds {float(runner.ANALYSIS_INTERVAL):.1f}" in body.decode()


def test_metrics_expose_the_control_plane_counters(plane):
    call(runner.handle_admin_pause)
    plane.state.system_health.safe_mode = True
    call(runner.handle_admin_resume)
    text = call(runner.handle_metrics)[1].decode()
    assert "manual_pause_active 1" in text
    assert 'admin_commands_total{command="pause", result="success"} 1' in text
    assert 'admin_commands_total{command="resume", result="blocked_safe_mode"} 1' in text


# ---------------------------------------------------------------------------
# Хаос
# ---------------------------------------------------------------------------

def test_chaos_is_off_by_default(plane):
    routes = runner.build_http_routes()
    assert set(routes) == {("GET", "/metrics"), ("GET", "/admin/status"),
                           ("POST", "/admin/pause"), ("POST", "/admin/resume")}
    assert call(runner.handle_chaos_inject)[0] == 403
    assert call(runner.handle_chaos_stop)[0] == 403


def test_chaos_injection_sets_the_flag_runtime_heartbeat_reads(plane, monkeypatch):
    """Флаг _chaos_was_active пишет обработчик, читает runtime_heartbeat_loop — связь, которую перенос обязан сохранить."""
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    chaos = FakeChaos()
    monkeypatch.setattr(runner, "get_chaos_engine", lambda: chaos)
    monkeypatch.setattr(runner, "log_task_dump", lambda *args, **kwargs: None)
    assert ("POST", "/admin/chaos/inject") in runner.build_http_routes()
    status, body = call(runner.handle_chaos_inject)
    assert status == 200 and json.loads(body)["incident_id"] == "inc-test"
    assert runner._chaos_was_active is True
    assert "_chaos_was_active" in runner.runtime_heartbeat_loop.__code__.co_names


def test_second_chaos_injection_is_a_conflict(plane, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setattr(runner, "get_chaos_engine", lambda: FakeChaos(inject_error=RuntimeError("already active")))
    assert call(runner.handle_chaos_inject)[0] == 409
    assert runner._chaos_was_active is False


def test_stopping_chaos_outside_safe_mode_enforces_it_and_resets_the_flag(plane, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setattr(runner, "get_chaos_engine", lambda: FakeChaos(stopped=True))
    runner._chaos_was_active = True
    assert call(runner.handle_chaos_stop)[0] == 200
    assert plane.machine.transitions == [(runner.SystemStateEnum.SAFE_MODE, "handle_chaos_stop")]
    assert runner._chaos_was_active is False


def test_stopping_without_active_chaos_is_404(plane, monkeypatch):
    monkeypatch.setenv("CHAOS_ENABLED", "true")
    monkeypatch.setattr(runner, "get_chaos_engine", lambda: FakeChaos(stopped=False))
    assert call(runner.handle_chaos_stop)[0] == 404
    assert plane.machine.transitions == []
