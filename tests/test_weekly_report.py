"""
Еженедельный отчёт по стратегиям (шаг 4 плана обучения, 11.09.2026).

Сделки текущего режима — по стратегиям в деньгах и в R; сигналы журнала — по судьбам
с исходами по свечам, чтобы видеть, не отсекает ли фильтр прибыльные сигналы.
"""
import asyncio
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import database
import journal
from analytics import weekly_report as wr
from loops import periodic

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "weekly.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    journal._recent.clear()
    yield database
    journal._recent.clear()
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def add_closed(symbol, strategy, pnl, on_exchange=True):
    """LONG 50 $ от 100 со стопом 98: риск 1 $, PnL в долларах = результат в R."""
    trade_id = database.add_trade(symbol, "LONG", 100.0, 98.0, 104.0, position_size=50.0, leverage=2,
                                  strategy_name=strategy, exchange_order_id="order-1" if on_exchange else None)
    database.close_trade(trade_id, 101.0, "EXCHANGE_CLOSE", pnl)


def fate(status, code, outcome, rr=2.0, strategy="trend_following"):
    return {"status": status, "reason_code": code, "strategy": strategy, "rr_ratio": rr, "outcome": outcome}


def test_the_report_is_due_on_monday_morning():
    wednesday = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    assert wr.seconds_until_next_report(wednesday) == (4 * 24 + 18) * 3600
    assert wr.seconds_until_next_report(datetime(2026, 9, 14, 5, 0, tzinfo=UTC)) == 3600
    assert wr.seconds_until_next_report(datetime(2026, 9, 14, 6, 0, tzinfo=UTC)) == 7 * 24 * 3600


def test_a_trade_result_in_r():
    trade = {"entry": 100.0, "original_stop": 98.0, "stop": 99.0, "position_size": 50.0, "pnl": 2.0}
    assert wr.trade_r(trade) == pytest.approx(2.0), "риск — от исходного стопа, не от подтянутого"
    assert wr.trade_r({"entry": 100.0, "stop": 0.0, "position_size": 50.0, "pnl": 1.0}) is None


def test_trades_are_grouped_by_strategy_in_the_current_mode(db):
    add_closed("ADAUSDT", "trend_following", 2.0)
    add_closed("DOTUSDT", "trend_following", -1.0)
    add_closed("ARBUSDT", None, -0.5)
    add_closed("SOLUSDT", "breakout", 5.0, on_exchange=False)
    lines = wr.trades_section(database.get_closed_trades_since("1970-01-01", on_exchange=True), capital=100.0)
    assert "• trend_following: 2 сд., прибыльных 50 %, PnL +1,00 $, в среднем +0,50 R" in lines
    assert any(line.startswith("• без стратегии: 1 сд.") for line in lines)
    assert not any("breakout" in line for line in lines), "бумажная сделка — не в отчёт биржевого режима"
    assert lines[-1] == "Итого: 3 сд., PnL +0,50 $ (+0,5 % счёта)"


def test_a_filter_that_cuts_winners_is_flagged():
    fates = ([fate("SENT", None, "LOSS")] * 3 + [fate("BLOCKED", "RiskCore", "WIN")] * 6
             + [fate("BLOCKED", "RiskCore", "LOSS")] * 4 + [fate("SKIPPED", "no_room", None)] * 2)
    lines = wr.signals_section(fates)
    assert lines[0] == "Сигналы: 15 — взято 3, отказ гейткипера 10, отсев генератора 2; ждут разметки 2."
    risk_core = next(line for line in lines if line.startswith("• RiskCore"))
    assert "цель 6, стоп 4" in risk_core and "ожидание +0,80 R" in risk_core and "⚠️" in risk_core
    assert "⚠️" not in next(line for line in lines if line.startswith("• взятые"))
    assert "• no_room: 2 — цель 0, стоп 0, без касаний 0" in lines


def test_too_few_decided_signals_are_not_flagged():
    lines = wr.signals_section([fate("BLOCKED", "META", "WIN")] * (wr.FLAG_MIN_DECIDED - 1))
    assert not any("⚠️" in line for line in lines)


def test_the_whole_report_from_the_database(db, monkeypatch):
    import capital
    import trading_mode
    monkeypatch.setattr(trading_mode, "sends_real_orders", lambda: True)
    monkeypatch.setattr(capital, "get_current_balance", lambda: 100.0)
    add_closed("ADAUSDT", "trend_following", 2.0)
    journal.record_signal(symbol="DOTUSDT", side="SHORT", entry=100.0, stop=102.0, target=96.0,
                          status=journal.BLOCKED, reason_code="RiskCore", strategy="trend_following",
                          timestamp=datetime.now(UTC) - timedelta(hours=30))
    signal_ts = database.get_signals_from_db("1970-01-01")[0]["timestamp"]
    database.save_signal_outcome({"signal_ts": signal_ts, "symbol": "DOTUSDT", "direction": "SHORT",
                                  "entry": 100.0, "tp": 96.0, "sl": 102.0, "checked_at": signal_ts,
                                  "outcome": "WIN"})
    report = wr.build_weekly_report()
    assert "сделки на бирже" in report.splitlines()[0]
    assert "• trend_following: 1 сд., прибыльных 100 %, PnL +2,00 $, в среднем +2,00 R" in report
    assert "• RiskCore: 1 — цель 1, стоп 0, без касаний 0, ожидание +2,00 R" in report


def run(coro, timeout=5.0):
    return asyncio.run(asyncio.wait_for(coro, timeout))


def test_the_weekly_report_is_not_sent_when_shutdown_is_already_set(monkeypatch):
    monkeypatch.setattr(wr, "send_weekly_report", lambda: pytest.fail("отчёт при остановке"))

    async def scenario():
        shutdown = asyncio.Event()
        shutdown.set()
        await periodic.weekly_report_loop(lambda: True, shutdown)

    run(scenario())


def test_the_weekly_report_is_sent_when_due(monkeypatch):
    sent = []
    monkeypatch.setattr(wr, "seconds_until_next_report", lambda now: 0.01)

    async def scenario():
        shutdown = asyncio.Event()

        def send():
            sent.append("report")
            shutdown.set()

        monkeypatch.setattr(wr, "send_weekly_report", send)
        await periodic.weekly_report_loop(lambda: True, shutdown)

    run(scenario())
    assert sent == ["report"]


def test_the_runner_registers_the_weekly_report():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert 'periodic.weekly_report_loop(_is_running, get_shutdown_event()), name="WeeklyReport"' in text


def test_the_signal_carries_its_strategy_to_the_trade_journal():
    generator = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    start = generator.index("signal_data = {", generator.index("def generate_signals_for_symbols"))
    block = generator[start:generator.index("}", start)]
    assert '"strategy_name": strategy_name,' in block
    assert 'strategy_name=signal_data.get("strategy_name")' in (ROOT / "execution" / "gatekeeper.py").read_text(
        encoding="utf-8")


def test_a_risk_core_refusal_names_the_violation():
    """В журнале было «Risk state: LOCKED, violations: 1» — без самого нарушения."""
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert '"; ".join(map(str, violation_report.violations[:2]))' in source
