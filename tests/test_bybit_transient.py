"""
Временные ошибки Bybit — один источник кодов (exchange.bybit_client.TRANSIENT_RET_CODES), 30.09.2026: загрузка
истории упала на 10016 «svc error» посреди прогона, потому что повторялись только 10006/10018, а в каждом клиенте
был свой список.
"""
import pytest

from exchange import bybit_client as bc
from tests.fake_bybit import FakeBybit, make_client


def test_the_codes_have_one_source():
    assert bc.RATE_LIMIT_RET_CODES == {10006, 10018} and bc.SERVER_ERROR_RET_CODES == {10016}
    from backtest import history
    assert history.RATE_LIMIT_CODES is bc.TRANSIENT_RET_CODES and history.BASE_URL == bc.MAINNET_REST


def test_a_read_retries_an_internal_exchange_error():
    fake = FakeBybit()
    fake.add_instrument("SOLUSDT", mark=100.0)
    fake.fault("GET", "/v5/market/tickers", "svc_error")
    client = make_client(fake)
    assert client.get_mark_price("SOLUSDT") == 100.0
    assert len(fake.calls("GET", "/v5/market/tickers")) == 2


def test_an_order_hit_by_an_internal_error_goes_to_reconciliation_not_a_blind_retry():
    fake = FakeBybit()
    fake.add_instrument("SOLUSDT", mark=100.0)
    fake.fault("POST", "/v5/order/create", "svc_error")
    client = make_client(fake)
    with pytest.raises(bc.OrderStateUnknown):
        client.place_order(symbol="SOLUSDT", side="Buy", qty=1, order_type="Market", client_order_id="link-1")
    assert len(fake.calls("POST", "/v5/order/create")) == 1


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


class _Session:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _Resp(self.payloads.pop(0))


@pytest.mark.parametrize("code", sorted(bc.TRANSIENT_RET_CODES))
def test_history_loader_retries_every_transient_code(code):
    from backtest import history
    session = _Session([{"retCode": code, "retMsg": "x"}, {"retCode": 0, "result": {"list": []}}])
    api = history.BybitHistory(session=session, sleep=lambda s: None)
    assert api.get("/v5/market/kline", {}) == {"list": []} and session.calls == 2


def test_research_http_client_retries_a_transient_code(monkeypatch):
    from backtest import xfunding
    answers = iter([b'{"retCode": 10016, "retMsg": "svc error"}', b'{"retCode": 0, "result": {"list": []}}'])

    class _Open:
        def __init__(self, *a, **k):
            self.body = next(answers)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self.body

    monkeypatch.setattr(xfunding.urllib.request, "urlopen", _Open)
    monkeypatch.setattr(xfunding.time, "sleep", lambda s: None)
    assert xfunding.http_get("https://example.invalid/x", {}) == {"retCode": 0, "result": {"list": []}}
