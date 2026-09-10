"""
Клиент Bybit на подменной бирже (tests/fake_bybit.py): подпись, повторы,
неоднозначный исход создания ордера, формат чисел, пагинация, плечо.
Каждая проверка — след находки разведки торгового пути 10.09.2026.
"""
import itertools
from decimal import Decimal

import pytest

from exchange.bybit_client import BybitUnavailable, OrderStateUnknown, fmt_number
from tests.fake_bybit import FakeBybit, make_client


@pytest.fixture
def fake():
    f = FakeBybit()
    f.add_instrument("SOLUSDT", mark=100.0)
    return f


@pytest.fixture
def client(fake):
    return make_client(fake)


def _ticking(client):
    clock = itertools.count(1_700_000_000_000, 1000)
    client._now_ms = lambda: next(clock)


# ---------------------------------------------------------------------------
# Подпись и повторы
# ---------------------------------------------------------------------------

def test_every_private_request_is_signed_over_what_was_sent(client, fake):
    client.get_positions()
    client.place_order("SOLUSDT", "Buy", Decimal("0.3"), stop_loss=Decimal("97.12"), client_order_id="mb-sign")
    signed = [r for r in fake.requests if r.sign_ok is not None]
    assert signed and all(r.sign_ok for r in signed)


def test_retry_rebuilds_timestamp_and_signature(client, fake):
    """Раньше подпись строили один раз до цикла: поздняя попытка выходила из recv_window."""
    _ticking(client)
    fake.fault("GET", "/v5/position/list", "http500", times=2)
    client.get_positions()
    attempts = fake.calls("GET", "/v5/position/list")
    assert len(attempts) == 3
    stamps = [r.headers["X-BAPI-TIMESTAMP"] for r in attempts]
    assert len(set(stamps)) == 3, "у каждой попытки своя метка времени"
    assert all(r.sign_ok for r in attempts)


@pytest.mark.parametrize("kind", ["timeout", "conn"])
def test_reads_survive_network_errors(client, fake, kind):
    """Раньше таймаут и обрыв соединения при чтении не повторялись вовсе."""
    fake.fault("GET", "/v5/market/tickers", kind)
    assert client.get_mark_price("SOLUSDT") == 100.0
    assert len(fake.calls("GET", "/v5/market/tickers")) == 2


def test_reads_give_up_after_all_attempts(client, fake):
    fake.fault("GET", "/v5/position/list", "timeout", times=10)
    with pytest.raises(BybitUnavailable):
        client.get_positions()
    assert len(fake.calls("GET", "/v5/position/list")) == 4


# ---------------------------------------------------------------------------
# Создание ордера: вслепую не повторяется
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["timeout", "conn", "timeout_after", "http500", "http500_after"])
def test_ambiguous_create_is_never_resent(client, fake, kind):
    fake.fault("POST", "/v5/order/create", kind)
    with pytest.raises(OrderStateUnknown) as info:
        client.place_order("SOLUSDT", "Buy", Decimal("0.3"), client_order_id="mb-amb")
    assert info.value.order_link_id == "mb-amb"
    assert len(fake.calls("POST", "/v5/order/create")) == 1, "повтор мог бы открыть вторую позицию"


def test_rate_limited_create_is_retried_with_fresh_signature(client, fake):
    """Отказ лимитера — запрос точно не принят; повтор безопасен и с новой подписью."""
    _ticking(client)
    fake.fault("POST", "/v5/order/create", "ratelimit")
    result = client.place_order("SOLUSDT", "Buy", Decimal("0.3"), client_order_id="mb-rl")
    attempts = fake.calls("POST", "/v5/order/create")
    assert len(attempts) == 2 and all(r.sign_ok for r in attempts)
    assert attempts[0].headers["X-BAPI-TIMESTAMP"] != attempts[1].headers["X-BAPI-TIMESTAMP"]
    assert result.order_id == fake.orders["mb-rl"]["orderId"]


def test_duplicate_link_id_means_the_exchange_already_has_it(client, fake):
    client.place_order("SOLUSDT", "Buy", Decimal("0.3"), client_order_id="mb-dup")
    with pytest.raises(OrderStateUnknown):
        client.place_order("SOLUSDT", "Buy", Decimal("0.3"), client_order_id="mb-dup")


def test_bad_link_id_is_rejected_before_sending(client, fake):
    with pytest.raises(ValueError):
        client.place_order("SOLUSDT", "Buy", Decimal("0.3"), client_order_id="x" * 37)
    assert fake.calls("POST", "/v5/order/create") == []


def test_order_body_is_one_way_mode_with_plain_numbers(client, fake):
    client.place_order("SOLUSDT", "Buy", 1e-05, stop_loss=Decimal("97.10"), take_profit=0.1,
                       client_order_id="mb-fmt")
    body = fake.calls("POST", "/v5/order/create")[0].body
    assert body["positionIdx"] == 0
    assert body["qty"] == "0.00001", "str(float) дал бы '1e-05' — биржа такое не примет"
    assert body["stopLoss"] == "97.1"
    assert body["takeProfit"] == "0.1"


@pytest.mark.parametrize("value, text", [
    (Decimal("0.30"), "0.3"), (1e-05, "0.00001"), (Decimal("100"), "100"), (Decimal("1E+2"), "100"), (0, "0"),
])
def test_fmt_number(value, text):
    assert fmt_number(value) == text


# ---------------------------------------------------------------------------
# Сверка, пагинация, плечо, фильтры
# ---------------------------------------------------------------------------

def test_find_order_falls_back_to_history(client, fake):
    """После перезапуска сервера биржи исполненные ордера видны только в order/history."""
    client.place_order("SOLUSDT", "Buy", Decimal("0.3"), client_order_id="mb-hist")
    fake.realtime_misses = True
    found = client.find_order("SOLUSDT", "mb-hist")
    assert found and found["orderId"] == fake.orders["mb-hist"]["orderId"]
    assert client.find_order("SOLUSDT", "mb-none") is None


def test_positions_follow_the_cursor(client, fake):
    """Раньше читалась первая страница: позиция со второй считалась закрытой."""
    fake.page_size = 2
    fake.positions = [{"symbol": f"S{i}USDT", "side": "Buy", "size": "1", "avgPrice": "1"} for i in range(5)]
    assert [p.symbol for p in client.get_positions()] == [f"S{i}USDT" for i in range(5)]
    assert len(fake.calls("GET", "/v5/position/list")) == 3


def test_unchanged_leverage_is_not_an_error(client, fake):
    client.set_leverage("SOLUSDT", Decimal("5"))
    client.set_leverage("SOLUSDT", Decimal("5"))  # 110043 — «не изменилось»
    assert fake.leverage["SOLUSDT"] == "5"
    body = fake.calls("POST", "/v5/position/set-leverage")[0].body
    assert body["buyLeverage"] == body["sellLeverage"] == "5"


def test_instrument_filters_have_no_defaults(client, fake):
    filters = client.get_instrument_filters("SOLUSDT")
    assert filters.tick_size == Decimal("0.01") and filters.qty_step == Decimal("0.1")
    assert filters.max_market_qty == Decimal("5000"), "для рыночного ордера — maxMktOrderQty"
    with pytest.raises(RuntimeError):
        client.get_instrument_filters("NOPEUSDT")
