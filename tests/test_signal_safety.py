"""
Предохранители сигнальной торговли (аудит 29.09.2026, пакет 7). Торговля выключена; эти проверки — условия,
без которых её нельзя включать: свой субсчёт, стратегии по одной, пауза переживает перезапуск, исполнение
подтверждает биржа, лимиты считают только ушедшие ордера, генератор один за раз, долгий цикл — не пауза.
"""
import ast
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Свой субсчёт
# ---------------------------------------------------------------------------

def _clear_machine():
    from system_state_machine import SystemState as MachineState
    machine = SimpleNamespace(state=MachineState.RUNNING, trading_paused=False)
    system = SimpleNamespace(system_health=SimpleNamespace(trading_paused=False))
    return dict(system_state=system, state_machine=machine, include_risk_core=False)


def test_signals_do_not_trade_on_the_account_shared_with_the_portfolio(monkeypatch):
    from execution.kill_switch import trading_halt_reason
    monkeypatch.delenv("SIGNAL_ACCOUNT_DEDICATED", raising=False)
    reason = trading_halt_reason(**_clear_machine())
    assert reason and "субсчёт" in reason, reason
    monkeypatch.setenv("SIGNAL_ACCOUNT_DEDICATED", "true")
    assert trading_halt_reason(**_clear_machine()) is None


def test_shared_account_follows_the_account_flag_not_the_trading_switch(monkeypatch):
    """Раньше «общий счёт» выводился из выключателя: включение торговли переводило капитал на equity И14."""
    import capital
    monkeypatch.setenv("SIGNAL_TRADING_ENABLED", "true")
    monkeypatch.delenv("SIGNAL_ACCOUNT_DEDICATED", raising=False)
    assert capital.account_shared_with_portfolio()
    monkeypatch.setenv("SIGNAL_ACCOUNT_DEDICATED", "true")
    assert not capital.account_shared_with_portfolio()


# ---------------------------------------------------------------------------
# Стратегии по одной
# ---------------------------------------------------------------------------

def test_no_strategy_is_enabled_by_default(monkeypatch):
    from strategies.strategy_manager import enabled_strategies
    monkeypatch.delenv("SIGNAL_STRATEGIES", raising=False)
    assert enabled_strategies() == set()
    monkeypatch.setenv("SIGNAL_STRATEGIES", " trend_following , legacy,")
    assert enabled_strategies() == {"trend_following", "legacy"}


def _sent_strategies(monkeypatch, enabled):
    from tests.test_setup_equivalence import run_scenarios
    monkeypatch.setenv("SIGNAL_STRATEGIES", enabled)
    return {e[6] for e in run_scenarios(monkeypatch) if e[0] == "GK"}


def test_without_enabled_strategies_the_generator_sends_nothing(monkeypatch):
    """Менеджер стратегий создаётся при импорте — флаг обязан читаться при оценке, а не в конструкторе."""
    assert _sent_strategies(monkeypatch, "") == set()


def test_only_the_enabled_strategies_reach_the_gatekeeper(monkeypatch):
    everything = _sent_strategies(monkeypatch, "trend_following,mean_reversion,momentum_breakout,legacy")
    assert "legacy" in everything and len(everything) >= 2, everything
    assert _sent_strategies(monkeypatch, "legacy") == {"legacy"}
    without_legacy = _sent_strategies(monkeypatch, "trend_following,mean_reversion,momentum_breakout")
    assert without_legacy and "legacy" not in without_legacy, without_legacy


# ---------------------------------------------------------------------------
# Ручная пауза переживает перезапуск
# ---------------------------------------------------------------------------

def test_pause_and_resume_are_written_to_the_pause_file(tmp_path):
    import runner
    marker = tmp_path / "manual_pause"
    assert runner.pause_trading_manually()
    try:
        assert marker.exists(), "пауза /pause не записана — перезапуск её снимет"
    finally:
        runner.resume_trading_manually()
    assert not marker.exists(), "после /resume файл паузы остался — следующий старт снова встанет на паузу"


def test_a_remembered_pause_is_restored_at_startup(monkeypatch):
    import runner
    from control_plane import pause_store
    synced = []
    machine = SimpleNamespace(sync_to_system_state=lambda state, **kw: synced.append(kw))
    monkeypatch.setattr(runner, "_control_plane_state", {"manual_pause_active": False})

    assert runner.restore_manual_pause(machine) is False
    assert runner._control_plane_state["manual_pause_active"] is False and synced == []

    pause_store.remember(True)
    assert runner.restore_manual_pause(machine) is True
    assert runner._control_plane_state["manual_pause_active"] is True
    assert synced == [{"manual_pause_active": True}]


def test_startup_restores_the_pause_right_after_the_loop_is_registered():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    main = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.AsyncFunctionDef) and n.name == "main")
    src = ast.get_source_segment(text, main)
    assert src.index("AsyncToSyncAdapter.set_main_loop(loop)") < src.index("restore_manual_pause(state_machine)")


def test_http_pause_and_resume_are_remembered(monkeypatch):
    from control_plane import http, pause_store
    from control_plane import state as cp_state
    monkeypatch.setattr(http, "get_state_machine", lambda: SimpleNamespace(sync_to_system_state=lambda *a, **k: None))
    monkeypatch.setitem(cp_state.control_plane_state, "manual_pause_active", False)
    state = SimpleNamespace(system_health=SimpleNamespace(safe_mode=False, trading_paused=False))
    asyncio.run(http.handle_admin_pause(state))
    assert pause_store.remembered()
    asyncio.run(http.handle_admin_resume(state))
    assert not pause_store.remembered()


def test_a_failed_write_is_logged_not_raised(monkeypatch, tmp_path, caplog):
    from control_plane import pause_store
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MANUAL_PAUSE_FILE", str(blocker / "manual_pause"))
    pause_store.remember(True)
    assert "не удалось сохранить" in caplog.text


def test_a_risk_core_halt_survives_a_restart_until_reset(tmp_path):
    """HALTED по ADR терминален, а перезапуск контейнера создавал новый Risk Core без защёлки."""
    from core.risk_core import RiskCore
    halt = tmp_path / "risk_halt"
    first = RiskCore(halt_file=halt)
    first._latch_halt("нарушение инварианта уровня HALTED")
    assert halt.exists()

    restarted = RiskCore(halt_file=halt)
    assert restarted.halt_latched and "нарушение инварианта" in restarted.halt_reason

    assert restarted.reset_halt(by="test")
    assert not halt.exists()
    assert not RiskCore(halt_file=halt).halt_latched


def test_the_production_risk_core_keeps_its_halt_on_disk(monkeypatch, tmp_path):
    import core.risk_core as rc
    monkeypatch.setattr(rc, "_risk_core", None)
    core = rc.get_risk_core()
    try:
        core._latch_halt("проверка")
        assert (tmp_path / "risk_halt").exists(), "рабочий экземпляр не записал защёлку — перезапуск её снимет"
    finally:
        core.reset_halt(by="test")


def test_a_risk_core_without_a_halt_file_writes_nothing(tmp_path, monkeypatch):
    from core.risk_core import RiskCore
    monkeypatch.chdir(tmp_path)
    RiskCore()._latch_halt("в памяти")
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Долгий цикл анализа — тревога, не пауза
# ---------------------------------------------------------------------------

def test_a_slow_analysis_cycle_alerts_without_pausing(monkeypatch):
    import runner
    captured = []

    async def capture(alerts):
        captured.extend(alerts)

    monkeypatch.setattr(runner, "dispatch_alerts", capture)
    monkeypatch.setattr(runner, "_should_send_alert", lambda key: True)
    monkeypatch.setattr(runner, "_mark_alert_sent", lambda key: None)
    asyncio.run(runner.evaluate_and_send_alerts(runner.MAX_ANALYSIS_TIME + 1))
    critical = [a for a in captured if a["type"] == "analysis_duration" and a["level"] == "CRITICAL"]
    assert critical, captured
    assert critical[0]["pause_trading"] is False


# ---------------------------------------------------------------------------
# Исполнение рыночного ордера подтверждает биржа
# ---------------------------------------------------------------------------

@pytest.fixture
def executor(monkeypatch):
    from execution import order_executor as oe
    from tests.fake_bybit import FakeBybit, make_client
    for name in ("LIVE_TRADING", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: None)
    fake = FakeBybit()
    fake.add_instrument("SOLUSDT", tick="0.01", step="0.1", min_qty="0.1", min_notional="5", mark=100.0)
    ex = oe.OrderExecutor(client=make_client(fake), sleep=lambda s: None)
    return ex, fake, oe


def _request(oe, **kw):
    base = dict(symbol="SOLUSDT", side="LONG", qty=0.3, entry_price=None,
                stop_loss=97.0, take_profit=106.0, leverage=5.0)
    base.update(kw)
    return oe.TradeRequest(**base)


def test_a_filled_market_order_reports_the_exchange_fill(executor):
    ex, fake, oe = executor
    fake.marks["SOLUSDT"] = 100.0
    result = ex.execute(_request(oe))
    assert result.success, result.error
    assert result.qty == 0.3 and result.avg_price == 100.0
    assert fake.calls("GET", "/v5/order/realtime") or fake.calls("GET", "/v5/order/history"), "исполнение не сверено"


def test_a_market_order_rejected_after_creation_is_a_failure(executor):
    """Раньше «создан» считалось «исполнен»: журнал писал FILLED, и фантомная сделка блокировала символ."""
    ex, fake, oe = executor
    fake.create_status = "Rejected"
    result = ex.execute(_request(oe))
    assert not result.success and not result.state_unknown
    assert "не исполнен" in result.error


def test_a_market_order_that_never_fills_is_reported_unknown(executor):
    ex, fake, oe = executor
    fake.create_status = "New"
    result = ex.execute(_request(oe))
    assert not result.success and result.state_unknown
    assert "не подтверждено" in result.error


def test_a_resting_limit_order_needs_no_fill_confirmation(executor):
    ex, fake, oe = executor
    fake.create_status = "New"
    result = ex.execute(_request(oe, entry_price=99.0))
    assert result.success, result.error
    assert result.avg_price is None


def test_the_journal_takes_the_exchange_fill_price():
    text = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert "if result.avg_price:\n                actual_entry = result.avg_price" in text


# ---------------------------------------------------------------------------
# Лимиты Risk Core считают только ушедшие ордера
# ---------------------------------------------------------------------------

def _bare_gatekeeper():
    from execution.gatekeeper import Gatekeeper
    return Gatekeeper.__new__(Gatekeeper)


def test_simulation_modes_place_nothing_and_say_so(monkeypatch):
    from execution import gatekeeper as gk
    from trading_mode import TradingMode
    monkeypatch.setattr(gk, "get_trading_mode", lambda: TradingMode.PAPER_TRADING)
    assert _bare_gatekeeper()._execute_order("BTCUSDT", {}, None) is None


def test_an_order_refused_before_the_exchange_is_not_placed(monkeypatch):
    from execution import gatekeeper as gk
    from trading_mode import TradingMode
    monkeypatch.setattr(gk, "get_trading_mode", lambda: TradingMode.TESTNET)
    assert _bare_gatekeeper()._execute_order("BTCUSDT", {"side": "LONG"}, None) is False


def test_the_action_journal_records_only_orders_that_went_out():
    text = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == "send_signal")
    src = ast.get_source_segment(text, node)
    placed = src.index("placed = self._execute_order(")
    refused = src.index("if placed is False:")
    assert placed < refused < src.index("return False", refused) < src.index("system_state.add_signal(")


# ---------------------------------------------------------------------------
# Один генератор за раз
# ---------------------------------------------------------------------------

def test_a_second_generation_is_skipped_while_the_first_still_runs(monkeypatch):
    import signal_generator as sg
    calls = []
    monkeypatch.setattr(sg, "_generate_signals", lambda *a: calls.append(a) or {"processed": 1})
    assert sg._GENERATION_LOCK.acquire(blocking=False)
    try:
        stats = sg.generate_signals_for_symbols({}, {}, True)
    finally:
        sg._GENERATION_LOCK.release()
    assert stats.get("skipped_busy") is True and calls == []
    assert sg.generate_signals_for_symbols({}, {}, True) == {"processed": 1}


def test_the_generation_lock_is_released_after_a_failure(monkeypatch):
    import signal_generator as sg

    def boom(*a):
        raise RuntimeError("generator failed")

    monkeypatch.setattr(sg, "_generate_signals", boom)
    with pytest.raises(RuntimeError):
        sg.generate_signals_for_symbols({}, {}, True)
    assert not sg._GENERATION_LOCK.locked()
