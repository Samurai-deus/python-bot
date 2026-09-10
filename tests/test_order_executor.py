"""
Исполнитель ордеров на подменной бирже (tests/fake_bybit.py) в режиме TESTNET.
Каждая проверка — след находки разведки торгового пути 10.09.2026.
"""
import pytest

from execution import order_executor as oe
from tests.fake_bybit import FakeBybit, make_client


@pytest.fixture(autouse=True)
def testnet_mode(monkeypatch):
    for name in ("LIVE_TRADING", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("DRY_RUN", "false")
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: None)


@pytest.fixture
def fake():
    f = FakeBybit()
    f.add_instrument("SOLUSDT", tick="0.01", step="0.1", min_qty="0.1", min_notional="5", mark=100.0)
    return f


@pytest.fixture
def executor(fake):
    ex = oe.OrderExecutor(client=make_client(fake), sleep=lambda s: None)
    assert ex._dry_run is False
    return ex


def request(**kw):
    base = dict(symbol="SOLUSDT", side="LONG", qty=0.3, entry_price=None,
                stop_loss=97.123456, take_profit=106.789, leverage=5.0)
    base.update(kw)
    return oe.TradeRequest(**base)


def creates(fake):
    return fake.calls("POST", "/v5/order/create")


# ---------------------------------------------------------------------------
# Округление
# ---------------------------------------------------------------------------

def test_qty_float_trap_is_gone(executor, fake):
    """Раньше math.floor(0.3 / 0.1) * 0.1 давало 0.2 — ордер на треть меньше."""
    result = executor.execute(request())
    assert result.success, result.error
    assert creates(fake)[0].body["qty"] == "0.3"
    assert result.qty == 0.3


def test_quarter_step_rounds_down_not_up(executor, fake):
    """Раньше при шаге 0,25 precision=0, и round() поднимал 0,75 до 1,0."""
    fake.add_instrument("QTRUSDT", tick="0.01", step="0.25", min_qty="0.25", mark=100.0)
    result = executor.execute(request(symbol="QTRUSDT", qty=0.76, stop_loss=97.0, take_profit=None))
    assert result.success, result.error
    assert creates(fake)[0].body["qty"] == "0.75"


def test_prices_are_aligned_to_tick(executor, fake):
    executor.execute(request())
    body = creates(fake)[0].body
    assert body["stopLoss"] == "97.12"
    assert body["takeProfit"] == "106.79"


# ---------------------------------------------------------------------------
# Отказы до биржи
# ---------------------------------------------------------------------------

def test_stale_signal_is_rejected_not_moved(executor, fake):
    """Раньше стоп за ценой переставлялся на ±0,5 % — и сделка открывалась."""
    result = executor.execute(request(stop_loss=101.0))
    assert not result.success and "устарел" in result.error
    assert creates(fake) == []


def test_no_mark_price_no_order(executor, fake):
    fake.marks.clear()
    result = executor.execute(request())
    assert not result.success
    assert creates(fake) == []


def test_missing_instrument_filters_no_order(executor, fake):
    """Раньше при ошибке брались шаги по умолчанию — и ордер уходил наугад."""
    result = executor.execute(request(symbol="NOPEUSDT"))
    assert not result.success and "Instrument filters unavailable" in result.error
    assert creates(fake) == []


def test_below_min_notional_no_order(executor, fake):
    fake.add_instrument("CHEAPUSDT", tick="0.0001", step="1", min_qty="1", min_notional="5", mark=0.5)
    result = executor.execute(request(symbol="CHEAPUSDT", qty=4, stop_loss=0.48, take_profit=None))
    assert not result.success and "номинал" in result.error
    assert creates(fake) == []


# ---------------------------------------------------------------------------
# Плечо
# ---------------------------------------------------------------------------

def test_leverage_is_set_once_before_the_order(executor, fake):
    """Раньше плечо не отправлялось никогда: биржа брала то, что стояло на счёте."""
    executor.execute(request())
    executor.execute(request(client_order_id="mb-second"))
    paths = [r.path for r in fake.requests if r.method == "POST"]
    assert paths.index("/v5/position/set-leverage") < paths.index("/v5/order/create")
    assert len(fake.calls("POST", "/v5/position/set-leverage")) == 1, "повторно то же плечо не ставится"
    assert fake.leverage["SOLUSDT"] == "5"


def test_leverage_failure_blocks_the_order(executor, fake):
    fake.leverage_error = 10001
    result = executor.execute(request())
    assert not result.success and "плечо" in result.error
    assert creates(fake) == []


def test_missing_leverage_blocks_the_order(executor, fake):
    result = executor.execute(request(leverage=None))
    assert not result.success
    assert creates(fake) == []


def test_liquidation_before_stop_blocks_the_order(executor, fake):
    """Стоп в 2,9 % при плече 40: ликвидация примерно в 2,5 % — раньше стопа."""
    result = executor.execute(request(leverage=40.0))
    assert not result.success and "ликвидация" in result.error
    assert creates(fake) == []


def test_leverage_above_exchange_maximum_blocks_the_order(executor, fake):
    fake.add_instrument("LOWLEVUSDT", max_leverage="3", mark=100.0)
    result = executor.execute(request(symbol="LOWLEVUSDT", leverage=5.0))
    assert not result.success and "максимума" in result.error
    assert creates(fake) == []


# ---------------------------------------------------------------------------
# Связь оборвалась при отправке
# ---------------------------------------------------------------------------

def test_accepted_order_with_lost_response_is_found(executor, fake):
    """Раньше это был «провал» — при живой позиции на бирже."""
    fake.fault("POST", "/v5/order/create", "timeout_after")
    result = executor.execute(request(client_order_id="mb-lost"))
    assert result.success, result.error
    assert result.order_id == fake.orders["mb-lost"]["orderId"]
    assert len(creates(fake)) == 1


def test_order_that_never_arrived_is_reported_unknown(executor, fake):
    fake.fault("POST", "/v5/order/create", "timeout")
    result = executor.execute(request(client_order_id="mb-gone"))
    assert not result.success and result.state_unknown
    assert "вручную" in result.error
    assert len(creates(fake)) == 1, "вслепую не повторяем"
    assert len(fake.calls("GET", "/v5/order/realtime")) == oe.RECONCILE_ATTEMPTS
    assert len(fake.calls("GET", "/v5/order/history")) == oe.RECONCILE_ATTEMPTS


def test_rejected_order_after_lost_response_is_a_plain_failure(executor, fake):
    fake.create_status = "Rejected"
    fake.fault("POST", "/v5/order/create", "timeout_after")
    result = executor.execute(request(client_order_id="mb-rej"))
    assert not result.success and not result.state_unknown
    assert "Rejected" in result.error


def test_duplicate_link_is_reconciled_to_the_existing_order(executor, fake):
    executor.execute(request(client_order_id="mb-twice"))
    result = executor.execute(request(client_order_id="mb-twice"))
    assert result.success
    assert result.order_id == fake.orders["mb-twice"]["orderId"]
    assert len(creates(fake)) == 2 and len(fake.orders) == 1
