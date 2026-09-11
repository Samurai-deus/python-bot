"""
Серия убытков — по событиям, а не по сделкам (11.09.2026).

11.09 пять коротких позиций по коррелированным альтам выбило одним отскоком рынка за
14 минут, и Risk Core засчитал пять убытков подряд — пауза 60 минут. По сути это одна
ставка. Теперь убытки, закрытые в пределах окна от самого нового убытка события,
считаются одним событием; порядок — по времени закрытия, сделки — текущего режима.
"""
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import config
import database
from core.risk_core import loss_streak

ROOT = pathlib.Path(__file__).resolve().parent.parent


def close(pnl, at):
    return {"pnl": pnl, "updated_at": at.isoformat()}


def t(hh, mm, ss=0):
    return datetime(2026, 9, 11, hh, mm, ss, tzinfo=UTC)


def test_one_market_move_is_one_loss_event():
    """11.09: DOT в 14:04:52, ADA/ARB/POL/APT в 13:51:07–08, до них UNI в плюс."""
    closes = [close(-0.18, t(14, 4, 52))] + [close(-0.13, t(13, 51, 8))] * 4 + [close(0.08, t(13, 36, 18))]
    assert loss_streak(closes) == (1, t(14, 4, 52))


def test_separate_losses_are_separate_events():
    closes = [close(-1, t(15, 0)), close(-1, t(14, 30)), close(-1, t(14, 0)), close(-1, t(13, 30))]
    assert loss_streak(closes)[0] == 4


def test_the_event_window_is_counted_from_its_newest_loss():
    """Убытки каждые 10 минут — не одно бесконечное событие: окно не скользит цепочкой."""
    closes = [close(-1, t(15, 0) - timedelta(minutes=10 * i)) for i in range(4)]  # 15:00 … 14:30
    assert loss_streak(closes, window_minutes=15)[0] == 2


def test_a_win_ends_the_streak():
    assert loss_streak([close(0.5, t(15, 0)), close(-1, t(14, 0))]) == (0, None)
    assert loss_streak([]) == (0, None)


def test_the_window_comes_from_settings():
    assert config.RISK_LOSS_EVENT_WINDOW_MINUTES == 15
    closes = [close(-1, t(15, 0)), close(-1, t(14, 50))]
    assert loss_streak(closes, window_minutes=5)[0] == 2
    assert loss_streak(closes)[0] == 1


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "losses.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def test_recent_closes_follow_the_close_time_and_the_mode(db):
    first_opened = database.add_trade("ADAUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0, exchange_order_id="a")
    second_opened = database.add_trade("DOTUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0, exchange_order_id="b")
    paper = database.add_trade("SOLUSDT", "LONG", 1.0, 0.9, 1.1, position_size=10.0)
    database.close_trade(second_opened, 1.1, "EXCHANGE_CLOSE", -0.1)
    database.close_trade(paper, 1.1, "TP", 0.5)
    database.close_trade(first_opened, 1.1, "EXCHANGE_CLOSE", -0.2)
    closes = database.get_recent_closes(on_exchange=True)
    assert [c["pnl"] for c in closes] == [-0.2, -0.1], "новые по закрытию первыми, бумажная — не в счёт"


def test_the_gatekeeper_counts_loss_events():
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert "loss_streak(" in source and "get_recent_closes(on_exchange=sends_real_orders())" in source
    assert "ORDER BY id DESC LIMIT 20" not in source
