"""
Сквозной цикл сделки в режиме TESTNET на подменной бирже (3.19, 10.09.2026).

Настоящий путь кода: гейткипер (_execute_order) → исполнитель → клиент Bybit →
имитация биржи (tests/fake_bybit.py, подпись каждого запроса проверяется) →
журнал сделок и трекер → закрытие по стопу на бирже → опрос трекера →
закрытие журнала по closed-pnl, ровно как в market_analysis_loop (runner.py).
Подменены только отправка в Telegram, база (временная SQLite) и синглтоны —
чтобы клиент, исполнитель и трекер смотрели в одну подменную биржу.
"""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import database
import exchange.bybit_client as bybit_client
import execution.gatekeeper as gatekeeper
import execution.order_executor as order_executor
import execution.position_tracker as position_tracker
from execution import exchange_ledger
from tests.fake_bybit import FakeBybit, make_client


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "e2e.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


@pytest.fixture
def exchange(db, monkeypatch):
    for name in ("LIVE_TRADING", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: None)

    fake = FakeBybit()
    fake.auto_positions = True
    fake.add_instrument("SOLUSDT", tick="0.01", step="0.1", min_qty="0.1", min_notional="5", mark=100.0)
    client = make_client(fake)
    executor = order_executor.OrderExecutor(client=client, sleep=lambda s: None)
    tracker = position_tracker.PositionTracker(client=client)
    monkeypatch.setattr(bybit_client, "_client", client)
    monkeypatch.setattr(order_executor, "_executor", executor)
    monkeypatch.setattr(position_tracker, "_tracker", tracker)

    messages = []
    monkeypatch.setattr(gatekeeper, "send_message_async", lambda text: messages.append(text))
    monkeypatch.setattr(gatekeeper, "AsyncToSyncAdapter", SimpleNamespace(call_async=lambda *a, **k: None))
    return SimpleNamespace(fake=fake, client=client, tracker=tracker, messages=messages)


def signal(**kw):
    base = {"entry": 100.0, "stop": 97.0, "target": 106.0, "side": "LONG",
            "position_size": 30.0, "leverage": 5.0, "strategy_name": "e2e"}
    base.update(kw)
    return base


def execute(sig, symbol="SOLUSDT"):
    # _execute_order не обращается к self — полный Gatekeeper здесь не нужен
    gatekeeper.Gatekeeper._execute_order(None, symbol, sig, None)


def creates(fake):
    return fake.calls("POST", "/v5/order/create")


def test_full_trade_cycle_on_the_exchange(exchange, db):
    fake = exchange.fake

    # 1. Сигнал доходит до биржи
    execute(signal())
    [create] = creates(fake)
    assert create.sign_ok
    assert create.body["qty"] == "0.3", "30 $ по цене 100 — 0,3 при шаге 0,1"
    assert create.body["stopLoss"] == "97", "стоп — те же 3 % от mark, по шагу цены"
    assert fake.leverage["SOLUSDT"] == "5"
    [trade] = db.get_open_trades()
    assert trade["symbol"] == "SOLUSDT" and trade["exchange_order_id"]
    assert exchange.tracker.active_count() == 1
    assert any("Ордер размещён" in m for m in exchange.messages)

    # 2. Повторный сигнал по символу не проходит — вторая позиция не открывается
    execute(signal())
    assert len(creates(fake)) == 1

    # 3. Биржа закрыла позицию по стопу — трекер видит, журнал закрывается по closed-pnl
    fake.positions = []
    fake.closed_pnl = [{"symbol": "SOLUSDT", "closedPnl": "-0.93", "avgExitPrice": "96.9",
                        "updatedTime": str(int(time.time() * 1000) + 1000)}]
    poll = exchange.tracker.poll()
    [closed] = poll.just_closed
    pnl = exchange_ledger.record_close(closed, exchange.client, sleep=lambda s: None)
    assert pnl == pytest.approx(-0.93)
    assert db.get_open_trades() == []
    conn = db.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, close_reason, pnl, close_price FROM trades")
        row = dict(cur.fetchone())
        cur.execute("SELECT status FROM positions")
        position_status = cur.fetchone()["status"]
    finally:
        conn.close()
    assert row["status"] == "CLOSED" and row["close_reason"] == "EXCHANGE_CLOSE"
    assert row["pnl"] == pytest.approx(-0.93) and row["close_price"] == pytest.approx(96.9)
    assert position_status == "CLOSED"
    assert exchange.tracker.active_count() == 0

    # 4. Символ снова свободен
    assert exchange_ledger.open_position_reason("SOLUSDT", exchange.client, exchange.tracker) is None


def test_stale_signal_never_reaches_the_exchange(exchange, db):
    """Цена уже за стопом — отказ до обращения к бирже, журнал пуст."""
    exchange.fake.marks["SOLUSDT"] = 96.0
    execute(signal())
    assert creates(exchange.fake) == []
    assert db.get_open_trades() == []


@pytest.mark.parametrize("mark, atr, sent", [
    (101.5, 2.0, True),    # 1,5 от входа при ATR 2 — исполняется
    (102.5, 2.0, False),   # ушла вверх на 2,5 — вход уже другой
    (97.5, 2.0, False),    # стоп (97) не пройден, но до него ушла больше чем на ATR
    (102.5, None, True),   # ATR в сигнале нет — правило не применяется
])
def test_signal_further_than_one_atr_from_entry(exchange, db, mark, atr, sent):
    """3.15: цена ушла от входа больше чем на 1 ATR в любую сторону — отказ до биржи."""
    exchange.fake.marks["SOLUSDT"] = mark
    execute(signal(atr=atr))
    assert bool(creates(exchange.fake)) is sent
    if not sent:
        assert db.get_open_trades() == []
        assert any("1 ATR" in m for m in exchange.messages)


def test_generator_puts_atr_into_the_signal():
    """Без ATR в сигнале правило молча не работает — поле кладёт signal_generator."""
    text = (Path(__file__).resolve().parent.parent / "signal_generator.py").read_text(encoding="utf-8")
    assert '"atr": atr_15m' in text
