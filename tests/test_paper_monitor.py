"""
Бумажный монитор, вынесенный из runner.py в loops/paper_monitor.py (пункт 5
плана отложенного, шаг 3). Журнал, свечи и отчёт — подменные.
"""
import asyncio
import pathlib
from types import SimpleNamespace

import pytest

from loops import paper_monitor

ROOT = pathlib.Path(__file__).resolve().parent.parent


def run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def make_state():
    resets = []
    state = SimpleNamespace(system_health=SimpleNamespace(is_running=True),
                            reset_signal_cooldown=resets.append)
    return state, resets


@pytest.fixture
def paper_mode(monkeypatch):
    import trading_mode
    monkeypatch.setattr(trading_mode, "sends_real_orders", lambda: False)


@pytest.fixture
def market(monkeypatch):
    """Одна открытая сделка SOLUSDT, свеча 5m; check_trades закрывает её с заданным PnL."""
    import data_loader
    import price_cache
    import trade_manager
    import trade_reporter

    calls = {"reports": [], "checked": []}
    closing = {"pnl": 1.5}
    monkeypatch.setattr(trade_manager, "get_open_trades", lambda: [{"symbol": "SOLUSDT"}])
    monkeypatch.setattr(data_loader, "get_candles",
                        lambda symbol, interval, limit: [[1_800_000_000_000, "100", "101", "96", "97", "10"]])
    monkeypatch.setattr(price_cache, "update", lambda symbol, price: None)

    def check_trades(symbol, price, low=None, high=None):
        calls["checked"].append((symbol, price, low, high))
        return [{"symbol": symbol, "side": "LONG", "pnl": closing["pnl"], "close_price": price,
                 "close_reason": "SL"}]

    monkeypatch.setattr(trade_manager, "check_trades", check_trades)
    monkeypatch.setattr(trade_reporter, "generate_trade_report", calls["reports"].append)
    return calls, closing


def test_idles_in_real_orders_mode_without_touching_the_journal(monkeypatch):
    import trading_mode
    import trade_manager
    monkeypatch.setattr(trading_mode, "sends_real_orders", lambda: True)
    monkeypatch.setattr(trade_manager, "get_open_trades", lambda: pytest.fail("в реальном режиме журнал ведёт биржа"))

    async def scenario():
        shutdown = asyncio.Event()
        shutdown.set()
        state, _ = make_state()
        await paper_monitor.paper_trading_monitor_loop(lambda: state, shutdown)

    run(scenario())


def test_closed_trade_resets_the_cooldown_and_is_reported(paper_mode, market):
    calls, _ = market

    async def scenario():
        shutdown = asyncio.Event()
        state, resets = make_state()

        def report(trade):
            calls["reports"].append(trade)
            shutdown.set()

        import trade_reporter
        trade_reporter.generate_trade_report = report
        await paper_monitor.paper_trading_monitor_loop(lambda: state, shutdown)
        return resets

    resets = run(scenario())
    assert resets == ["SOLUSDT"]
    assert [t["symbol"] for t in calls["reports"]] == ["SOLUSDT"]
    assert calls["checked"][0][:2] == ("SOLUSDT", 97.0)


def test_breakeven_close_is_not_reported(paper_mode, market, monkeypatch):
    calls, closing = market
    closing["pnl"] = 0.004

    async def scenario():
        shutdown = asyncio.Event()
        state, resets = make_state()
        import trade_manager
        original = trade_manager.check_trades

        def check_then_stop(*args, **kwargs):
            shutdown.set()
            return original(*args, **kwargs)

        monkeypatch.setattr(trade_manager, "check_trades", check_then_stop)
        await paper_monitor.paper_trading_monitor_loop(lambda: state, shutdown)
        return resets

    assert run(scenario()) == ["SOLUSDT"]
    assert calls["reports"] == []


def test_runner_registers_the_paper_monitor_with_its_state():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert ('paper_monitor.paper_trading_monitor_loop(_state, get_shutdown_event()), '
            'name="PaperTradingMonitor"') in text
    assert "async def paper_trading_monitor_loop(" not in text, "копия осталась в runner.py"
