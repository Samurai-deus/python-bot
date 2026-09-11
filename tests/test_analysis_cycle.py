"""
Цикл анализа рынка (runner.run_market_analysis, runner.market_analysis_loop) —
поведение как есть, до переноса из runner (пункт 5 плана отложенного,
docs/DEFERRED_PLAN.md, шаг 8а).

До 11.09.2026 у цикла были только текстовые проверки (метки liveness, вызовы
базы в пуле потоков, очистка трасс). Здесь зафиксировано, что перенос обязан
сохранить: какие шаги оборота мягкие, а какие роняют его; когда ошибка ведёт в
защитный режим; снимок раз в 5 оборотов; метрики и метка healthcheck; адаптивный
интервал; автовозобновление торговли и ручная пауза; опрос позиций на бирже.

Все зависимости подменены на уровне runner: мозги экосистемы, загрузка свечей,
генератор сигналов, автомат состояний, Telegram. Интервалы и паузы укорочены.
"""
import asyncio
import logging
from types import SimpleNamespace

import pytest

import runner
import telegram_bot
import trading_mode
from control_plane import state as cp_state


class FakeMachine:
    def __init__(self, safe=False):
        self.is_safe_mode = safe
        self.transitions = []
        self.synced = []

    async def transition_to(self, target, *args, **kwargs):
        self.transitions.append((target, kwargs.get("owner")))
        self.is_safe_mode = target == runner.SystemStateEnum.SAFE_MODE
        return True

    def sync_to_system_state(self, state, manual_pause_active):
        self.synced.append(manual_pause_active)


class FakeState:
    def __init__(self, consecutive_errors=0, total_cycles=0, safe_mode=False, trading_paused=False):
        self.system_health = SimpleNamespace(consecutive_errors=consecutive_errors, safe_mode=safe_mode,
                                             trading_paused=trading_paused, is_running=True)
        self.performance_metrics = SimpleNamespace(total_cycles=total_cycles)
        self.errors, self.cycles = [], []
        self.resets = 0
        self.correlations = None

    def record_error(self, error):
        self.errors.append(error)
        self.system_health.consecutive_errors += 1

    def reset_errors(self):
        self.resets += 1
        self.system_health.consecutive_errors = 0

    def increment_cycle(self, success=True):
        self.cycles.append(success)
        self.performance_metrics.total_cycles += 1

    def update_market_correlations(self, correlations):
        self.correlations = correlations

    def create_snapshot(self):
        return {"snapshot": self.performance_metrics.total_cycles}


def recording_send(sent):
    """Как send_message_async: вызов сразу отдаёт корутину; текст пишем в момент вызова."""
    def send(text):
        sent.append(text)

        async def delivered():
            return None
        return delivered()
    return send


def raises(exc):
    def fail(*args, **kwargs):
        raise exc
    return fail


# ---------------------------------------------------------------------------
# Один оборот: run_market_analysis
# ---------------------------------------------------------------------------

@pytest.fixture
def cycle(monkeypatch):
    import database
    from core import decision_trace, signal_snapshot_store

    env = SimpleNamespace(state=FakeState(), machine=FakeMachine(), sent=[], alerts=[], volatility=[],
                          generated=[], candles_loaded=[], saved=[], cleaned=[], pruned=[])
    env.candles = {"SOLUSDT": {"15m": ["candle"]}}
    env.correlations = {"SOLUSDT": {"ETHUSDT": 0.8}}
    env.decision = SimpleNamespace(can_trade=True, reason="", recommendations=[])
    env.decision_core = SimpleNamespace(should_i_trade=lambda system_state: env.decision)
    env.regime_brain = SimpleNamespace(analyze=lambda symbols, candles, state: SimpleNamespace(
        trend_type="UP", volatility_level="HIGH", risk_sentiment="NEUTRAL"))
    env.risk_brain = SimpleNamespace(analyze=lambda symbols, candles, state: SimpleNamespace(
        total_risk_pct=1.0, active_positions=0, is_overloaded=False))
    env.cognitive = SimpleNamespace(analyze=lambda state: SimpleNamespace(overtrading_score=0.0, should_pause=False))
    env.opportunity = SimpleNamespace()
    env.gatekeeper = SimpleNamespace(get_stats=lambda: {"total": 0, "approved": 0, "blocked": 0})

    def load(symbols, timeframes, limit, workers):
        env.candles_loaded.append(list(symbols))
        return env.candles

    def generate(**kwargs):
        env.generated.append(kwargs)
        return {"processed": 1, "signals_sent": 0, "signals_blocked": 0, "errors": 0}

    for name, value in {
        "system_state": env.state, "_active_symbols": ["SOLUSDT"], "is_good_time": lambda: True,
        "get_decision_core": lambda: env.decision_core, "get_market_regime_brain": lambda: env.regime_brain,
        "get_risk_exposure_brain": lambda: env.risk_brain, "get_cognitive_filter": lambda: env.cognitive,
        "get_opportunity_awareness": lambda: env.opportunity, "get_gatekeeper": lambda: env.gatekeeper,
        "get_candles_parallel": load, "check_all_symbols_for_spikes": lambda symbols, candles: None,
        "analyze_market_correlations": lambda symbols, candles, tf: env.correlations,
        "generate_signals_for_symbols": generate, "update_volatility_state": env.volatility.append,
        "send_message_async": recording_send(env.sent), "error_alert": env.alerts.append,
        "get_state_machine": lambda: env.machine, "RUNNING_TASKS": set(), "_shutdown_event": None,
        "MAX_CONSECUTIVE_ERRORS": 5,
    }.items():
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(signal_snapshot_store.SystemStateSnapshotStore, "save", env.saved.append)
    monkeypatch.setattr(database, "cleanup_old_snapshots", lambda keep_last_n: env.cleaned.append(keep_last_n))
    monkeypatch.setattr(decision_trace, "prune_decision_trace", env.pruned.append)
    return env


def analyse():
    return asyncio.run(asyncio.wait_for(runner.run_market_analysis(), 10.0))


def test_outside_trading_hours_the_cycle_is_skipped(cycle, monkeypatch):
    monkeypatch.setattr(runner, "is_good_time", lambda: False)
    assert analyse() is True
    assert cycle.candles_loaded == [] and cycle.state.cycles == []


def test_a_full_cycle_feeds_the_signal_generator_and_counts_success(cycle):
    assert analyse() is True
    assert cycle.candles_loaded == [["SOLUSDT"]]
    (kwargs,) = cycle.generated
    assert kwargs["all_candles"] is cycle.candles and kwargs["market_correlations"] is cycle.correlations
    assert kwargs["decision_core"] is cycle.decision_core and kwargs["gatekeeper"] is cycle.gatekeeper
    assert kwargs["opportunity_awareness"] is cycle.opportunity and kwargs["system_state"] is cycle.state
    assert kwargs["good_time"] is True
    assert cycle.state.correlations is cycle.correlations
    assert cycle.volatility == ["HIGH"]
    assert cycle.state.resets == 1 and cycle.state.cycles == [True]
    assert cycle.saved == [], "снимок — только каждый 5-й оборот"


def test_every_fifth_cycle_saves_a_snapshot_and_prunes_old_data(cycle):
    cycle.state.performance_metrics.total_cycles = 4
    assert analyse() is True
    assert cycle.saved == [{"snapshot": 5}]
    assert cycle.cleaned == [10] and cycle.pruned == [90]


def test_slow_data_loading_is_a_soft_failure(cycle, monkeypatch):
    monkeypatch.setattr(runner, "get_candles_parallel", raises(TimeoutError()))
    assert analyse() is False
    assert cycle.state.errors == ["Data loading timeout (non-critical)"]
    assert cycle.generated == [] and cycle.machine.transitions == []


def test_a_failing_brain_does_not_stop_the_cycle(cycle):
    cycle.regime_brain.analyze = raises(ValueError("bad candles"))
    assert analyse() is True
    assert cycle.volatility == [] and len(cycle.generated) == 1


def test_a_decision_core_veto_notifies_and_skips_the_signals(cycle):
    cycle.decision = SimpleNamespace(can_trade=False, reason="drawdown limit", recommendations=["wait"])
    assert analyse() is True
    assert cycle.generated == []
    assert len(cycle.sent) == 1 and "drawdown limit" in cycle.sent[0] and "• wait" in cycle.sent[0]
    assert cycle.state.cycles == [], "оборот с вето не считается ни успешным, ни неудачным"


def test_an_injected_decision_fault_enters_safe_mode_at_the_limit(cycle, monkeypatch):
    monkeypatch.setattr(runner, "MAX_CONSECUTIVE_ERRORS", 2)
    cycle.state.system_health.consecutive_errors = 1
    cycle.decision_core.should_i_trade = raises(RuntimeError("FAULT_INJECTION: decision_exception"))
    assert analyse() is False
    assert cycle.state.errors == ["FAULT_INJECTION: decision_exception"]
    assert cycle.machine.transitions == [(runner.SystemStateEnum.SAFE_MODE, "error_alert")]
    assert cycle.alerts == []


@pytest.mark.parametrize("limit, safe_mode", [(2, True), (5, False)])
def test_an_unexpected_error_alerts_the_owner_and_enters_safe_mode_at_the_limit(cycle, monkeypatch,
                                                                                limit, safe_mode):
    monkeypatch.setattr(runner, "MAX_CONSECUTIVE_ERRORS", limit)
    cycle.state.system_health.consecutive_errors = 1
    cycle.decision_core.should_i_trade = raises(RuntimeError("database is locked"))
    assert analyse() is False
    assert cycle.state.errors == ["database is locked"]
    assert len(cycle.alerts) == 1
    assert cycle.alerts[0].startswith("Критическая ошибка в цикле анализа: RuntimeError: database is locked")
    expected = [(runner.SystemStateEnum.SAFE_MODE, "error_alert")] if safe_mode else []
    assert cycle.machine.transitions == expected


def test_a_signal_generation_error_is_logged_and_the_cycle_still_counts(cycle, monkeypatch):
    monkeypatch.setattr(runner, "generate_signals_for_symbols", raises(ValueError("broken strategy")))
    assert analyse() is True
    assert cycle.state.cycles == [True] and cycle.state.errors == []


def test_slow_signal_generation_is_recorded_then_the_cycle_completes(cycle, monkeypatch):
    monkeypatch.setattr(runner, "generate_signals_for_symbols", raises(TimeoutError()))
    assert analyse() is True
    assert cycle.state.errors == ["Signal generation timeout (non-critical)"]
    assert cycle.state.resets == 1 and cycle.state.cycles == [True]


def test_an_exhausted_budget_defers_the_rest_of_the_cycle(cycle, monkeypatch):
    monkeypatch.setattr(runner, "ITERATION_BUDGET_SECONDS", -1.0)
    assert analyse() is False
    assert cycle.candles_loaded == [["SOLUSDT"]], "проверка после инициализации только предупреждает"
    assert cycle.generated == [] and cycle.state.cycles == []


def test_shutdown_during_the_cycle_cancels_it(cycle, monkeypatch):
    shutdown = asyncio.Event()
    shutdown.set()
    monkeypatch.setattr(runner, "_shutdown_event", shutdown)
    with pytest.raises(asyncio.CancelledError):
        analyse()
    assert cycle.candles_loaded == [] and cycle.state.errors == []


# ---------------------------------------------------------------------------
# Цикл: market_analysis_loop
# ---------------------------------------------------------------------------

@pytest.fixture
def loop(monkeypatch):
    env = SimpleNamespace(state=FakeState(), machine=FakeMachine(), sent=[], alerts=[], marks=[],
                          durations=[], recoveries=[], calls=0, script=[])
    env.adaptive = {"volatility_state": "MEDIUM", "adaptive_interval": 0.02, "recovery_cycles": 0}
    env.control = {"manual_pause_active": False}
    env.analysis = {"analysis_count": 0, "analysis_total_time": 0.0, "analysis_max_time": 0.0,
                    "last_analysis_duration": 0.0, "start_time": None}
    env.prometheus = {"analysis_duration_buckets": {b: 0 for b in cp_state.ANALYSIS_DURATION_BUCKETS},
                      "analysis_duration_sum": 0.0, "analysis_duration_count": 0,
                      "scheduler_stalls_total": 0, "analysis_cycles_total": 0}
    # runner держит ссылки на объекты control_plane.state — подменяем оба имени
    for runner_name, cp_name, value in (("_adaptive_system_state", "adaptive_system_state", env.adaptive),
                                        ("_control_plane_state", "control_plane_state", env.control),
                                        ("_analysis_metrics", "analysis_metrics", env.analysis),
                                        ("_prometheus_metrics", "prometheus_metrics", env.prometheus)):
        monkeypatch.setattr(runner, runner_name, value)
        monkeypatch.setattr(cp_state, cp_name, value)

    async def fake_run():
        env.calls += 1
        step = env.script[env.calls - 1]
        if env.calls >= len(env.script):
            env.state.system_health.is_running = False  # последний оборот — дальше цикл выходит
        return step(env.state) if callable(step) else step

    async def evaluate(duration):
        env.durations.append(duration)

    async def recover(reason, owner):
        env.recoveries.append(owner)
        return True

    send = recording_send(env.sent)
    for name, value in {
        "system_state": env.state, "run_market_analysis": fake_run, "_shutdown_event": None,
        "liveness": SimpleNamespace(mark=env.marks.append), "evaluate_and_send_alerts": evaluate,
        "get_state_machine": lambda: env.machine, "exit_safe_mode_via_recovery": recover,
        "send_message_async": send, "error_alert": env.alerts.append, "RUNNING_TASKS": set(),
        "ANALYSIS_INTERVAL": 0.02, "ADAPTIVE_INTERVAL_ENABLED": True, "ADAPTIVE_INTERVAL_MIN": 0.01,
        "ADAPTIVE_INTERVAL_MAX": 0.08, "ADAPTIVE_INTERVAL_MULTIPLIER": 2.0, "ADAPTIVE_STABLE_CYCLES": 2,
        "AUTO_RESUME_TRADING_ENABLED": True, "AUTO_RESUME_SUCCESS_CYCLES": 2, "AUTO_RESUME_SAFE_MODE_DELAY": 0,
        "ERROR_PAUSE": 0, "MAX_CONSECUTIVE_ERRORS": 5,
    }.items():
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setattr(telegram_bot, "send_message_async", send)
    monkeypatch.setattr(trading_mode, "get_trading_mode", lambda: "PAPER")

    def run(*script):
        env.script = list(script)

        async def scenario():
            await runner.market_analysis_loop()
            await asyncio.sleep(0.01)  # дать отработать задачам алертов и уведомлений

        asyncio.run(asyncio.wait_for(scenario(), 10.0))
        return env

    env.run = run
    return env


def failing(consecutive_errors):
    def step(state):
        state.system_health.consecutive_errors = consecutive_errors
        return False
    return step


def test_each_turn_updates_the_health_metrics_and_the_liveness_mark(loop):
    loop.run(True, True)
    assert loop.calls == 2
    assert loop.analysis["analysis_count"] == 2 and loop.analysis["start_time"] is not None
    assert loop.prometheus["analysis_cycles_total"] == 2 and loop.prometheus["analysis_duration_count"] == 2
    assert loop.marks == ["analysis"] * 3, "метка при старте и после каждого оборота"
    assert len(loop.durations) == 2, "алерты оцениваются после каждого оборота"


def test_errors_widen_the_adaptive_interval(loop):
    loop.run(failing(consecutive_errors=1))
    assert loop.adaptive["adaptive_interval"] == pytest.approx(0.04)


def test_the_adaptive_interval_never_exceeds_the_maximum(loop):
    loop.adaptive["adaptive_interval"] = 0.08
    loop.run(failing(consecutive_errors=1))
    assert loop.adaptive["adaptive_interval"] == pytest.approx(0.08)


def test_stable_cycles_narrow_the_adaptive_interval(loop):
    loop.adaptive["adaptive_interval"] = 0.04
    loop.run(True, True)
    assert loop.adaptive["adaptive_interval"] == pytest.approx(0.02)


def test_trading_resumes_after_enough_clean_cycles(loop, caplog):
    """
    Найдено этими тестами 11.09.2026: `from telegram_bot import send_message_async`
    в ветке опроса позиций (только TESTNET/LIVE) делал имя локальным для всей
    функции. В режиме бумажной торговли уведомление о возобновлении падало с
    UnboundLocalError — торговля возобновлялась, но оборот уходил в «Critical
    error» и паузу, а владелец не узнавал.
    """
    caplog.set_level(logging.INFO)
    loop.state.system_health.trading_paused = True
    loop.machine.is_safe_mode = True
    loop.run(True, True)
    assert loop.recoveries == ["market_analysis_loop"]
    assert loop.machine.synced == [False]
    assert loop.adaptive["recovery_cycles"] == 0
    assert any("Trading resumed" in text for text in loop.sent)
    assert "Critical error in market analysis loop" not in caplog.text


def test_a_manual_pause_is_never_auto_resumed(loop):
    loop.control["manual_pause_active"] = True
    loop.state.system_health.trading_paused = True
    loop.run(True, True, True)
    assert loop.recoveries == [] and loop.machine.synced == []
    assert loop.adaptive["recovery_cycles"] == 0 and loop.sent == []


def test_a_stale_manual_flag_is_cleared_while_trading_is_active(loop):
    loop.control["manual_pause_active"] = True
    loop.run(True)
    assert loop.control["manual_pause_active"] is False


def test_recovery_progress_is_reset_every_turn_while_in_safe_mode(loop):
    loop.state.system_health.trading_paused = True
    loop.state.system_health.safe_mode = True
    loop.run(True)
    assert loop.adaptive["recovery_cycles"] == 0


def test_a_failed_turn_resets_recovery_progress(loop):
    loop.state.system_health.trading_paused = True
    loop.adaptive["recovery_cycles"] = 1
    loop.run(failing(consecutive_errors=1))
    assert loop.adaptive["recovery_cycles"] == 0 and loop.recoveries == []


def test_trading_resumes_after_safe_mode_exit_when_auto_resume_is_off(loop, monkeypatch):
    """Та же ловушка с локальным send_message_async — во второй ветке возобновления."""
    monkeypatch.setattr(runner, "AUTO_RESUME_TRADING_ENABLED", False)
    loop.state.system_health.safe_mode = True
    loop.state.system_health.trading_paused = True

    def safe_mode_cleared(state):
        state.system_health.safe_mode = False
        return True

    loop.run(safe_mode_cleared)
    assert loop.machine.synced == [False]
    assert any("recovered from safe mode" in text for text in loop.sent)


def test_repeated_errors_pause_alert_the_owner_and_reset_the_counter(loop):
    loop.run(failing(consecutive_errors=5))
    assert loop.alerts == ["Multiple errors (5). Pausing 0s"]
    assert loop.state.resets == 1


def test_a_crashing_turn_does_not_stop_the_loop(loop, caplog):
    caplog.set_level(logging.INFO)
    loop.run(raises(RuntimeError("boom")), True)
    assert loop.calls == 2
    assert "Critical error in market analysis loop: RuntimeError: boom" in caplog.text
    assert loop.analysis["analysis_count"] == 1, "упавший оборот метрики не обновляет"


def test_positions_closed_on_the_exchange_are_journaled_and_reported(loop, monkeypatch):
    from execution import exchange_ledger, position_tracker
    closed = SimpleNamespace(symbol="SOLUSDT", side="LONG")
    tracker = SimpleNamespace(active_count=lambda: 1, poll=lambda: SimpleNamespace(just_closed=[closed]))
    recorded = []
    monkeypatch.setattr(trading_mode, "get_trading_mode", lambda: trading_mode.TradingMode.TESTNET)
    monkeypatch.setattr(position_tracker, "get_position_tracker", lambda: tracker)
    monkeypatch.setattr(exchange_ledger, "record_close", lambda position: recorded.append(position) or 1.5)
    loop.run(True)
    assert recorded == [closed]
    assert any("Позиция закрыта: SOLUSDT LONG" in text and "+1.50" in text for text in loop.sent)


def test_paper_trading_never_polls_the_exchange(loop, monkeypatch, caplog):
    from execution import position_tracker
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(position_tracker, "get_position_tracker", raises(AssertionError("polled in paper mode")))
    loop.run(True)
    assert "Position tracker poll error" not in caplog.text


def test_the_loop_does_nothing_when_the_bot_is_stopping(loop):
    loop.state.system_health.is_running = False
    loop.run(True)
    assert loop.calls == 0 and loop.marks == ["analysis"]
