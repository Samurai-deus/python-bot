"""
Bybit 10002 (метка времени вне recv_window): один повтор с новой подписью (аудит 29.09.2026).

27.09.2026 на проде запросы кошелька доходили до биржи через 8–10 с и отвергались с 10002, цикл
исполнителя падал до следующего часа. Биржа такой запрос не исполняет — повтор безопасен.
"""
import pytest

from exchange.bybit_client import BybitAPIError, BybitClient


class Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.text = str(payload)

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


STALE = {"retCode": 10002, "retMsg": "invalid request, please check your server timestamp or recv_window param"}
OK = {"retCode": 0, "retMsg": "OK", "result": {"list": []}}


class Session:
    def __init__(self, replies):
        self.replies = list(replies)
        self.stamps = []
        self.headers = {}

    def get(self, url, headers=None, timeout=None):
        self.stamps.append((headers or {}).get("X-BAPI-TIMESTAMP"))
        return Resp(self.replies.pop(0))


def client(replies, monkeypatch):
    cli = BybitClient(api_key="k", api_secret="s", demo=True)
    cli._session = Session(replies)
    clock = iter(range(1_000_000, 2_000_000, 7_000))
    monkeypatch.setattr(cli, "_now_ms", lambda: next(clock))
    return cli


def test_stale_request_is_retried_once_with_a_fresh_signature(monkeypatch):
    cli = client([STALE, OK], monkeypatch)
    assert cli._get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True) == {"list": []}
    first, second = cli._session.stamps
    assert first != second, "повтор подписан заново — с новой меткой времени"


def test_stale_request_twice_is_an_error(monkeypatch):
    cli = client([STALE, STALE, OK], monkeypatch)
    with pytest.raises(BybitAPIError) as err:
        cli._get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True)
    assert err.value.code == 10002 and len(cli._session.stamps) == 2, "повтор — ровно один"


def test_other_api_errors_are_not_retried(monkeypatch):
    cli = client([{"retCode": 10001, "retMsg": "params error"}, OK], monkeypatch)
    with pytest.raises(BybitAPIError):
        cli._get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True)
    assert len(cli._session.stamps) == 1
