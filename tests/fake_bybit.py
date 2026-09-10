"""
Подменная сессия requests с поведением Bybit v5 на путях, которые трогает бот.

Проверяет подпись каждого приватного запроса по тем байтам, что реально ушли.
Сбои задаются очередью: fault("POST", "/v5/order/create", "timeout_after") —
«биржа ордер приняла, ответ потерялся». Виды сбоев:
  timeout / conn        — запрос до биржи не дошёл;
  http500 / ratelimit   — биржа ответила ошибкой, ничего не сделав;
  timeout_after / http500_after — биржа сделала, ответ потерялся или испорчен.
"""
import hashlib
import hmac
import itertools
import json
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlsplit

import requests

API_KEY = "test-key"
API_SECRET = "test-secret"


class FakeResponse:
    def __init__(self, status_code, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


@dataclass
class Recorded:
    method: str
    path: str
    params: dict
    body: dict
    headers: dict
    sign_ok: Optional[bool]


class FakeBybit:
    def __init__(self):
        self.headers = {}
        self.requests = []
        self.instruments = {}
        self.marks = {}
        self.orders = {}
        self.positions = []
        self.leverage = {}
        self.page_size = None
        self.realtime_misses = False
        self.leverage_error = None
        self.create_status = "Filled"
        self.closed_pnl = []
        self._faults = []
        self._ids = itertools.count(1)

    # --- настройка ---
    def add_instrument(self, symbol, tick="0.01", step="0.1", min_qty="0.1", max_mkt="5000",
                       min_notional="5", max_leverage="50", status="Trading", mark=None):
        self.instruments[symbol] = {
            "symbol": symbol, "status": status,
            "priceFilter": {"tickSize": tick},
            "lotSizeFilter": {"qtyStep": step, "minOrderQty": min_qty, "maxOrderQty": "100000",
                              "maxMktOrderQty": max_mkt, "minNotionalValue": min_notional},
            "leverageFilter": {"maxLeverage": max_leverage},
        }
        if mark is not None:
            self.marks[symbol] = mark

    def fault(self, method, path, kind, times=1):
        self._faults.extend([(method, path, kind)] * times)

    def calls(self, method, path):
        return [r for r in self.requests if r.method == method and r.path == path]

    # --- интерфейс requests.Session ---
    def get(self, url, params=None, headers=None, timeout=None):
        return self._handle("GET", url, None, headers or {})

    def post(self, url, data=None, headers=None, timeout=None):
        return self._handle("POST", url, data, headers or {})

    # --- внутреннее ---
    def _take_fault(self, method, path):
        for i, (m, p, kind) in enumerate(self._faults):
            if m == method and p == path:
                del self._faults[i]
                return kind
        return None

    def _handle(self, method, url, data, headers):
        parts = urlsplit(url)
        path, query = parts.path, parts.query
        params = dict(parse_qsl(query))
        body_text = data.decode("utf-8") if isinstance(data, bytes) else (data or "")
        body = json.loads(body_text) if body_text else {}
        sign_ok = None
        if "X-BAPI-SIGN" in headers:
            payload = query if method == "GET" else body_text
            expected = hmac.new(
                API_SECRET.encode(),
                (headers["X-BAPI-TIMESTAMP"] + API_KEY + headers["X-BAPI-RECV-WINDOW"] + payload).encode(),
                hashlib.sha256,
            ).hexdigest()
            sign_ok = expected == headers["X-BAPI-SIGN"]
        self.requests.append(Recorded(method, path, params, body, dict(headers), sign_ok))

        kind = self._take_fault(method, path)
        if kind == "timeout":
            raise requests.Timeout("fake timeout")
        if kind == "conn":
            raise requests.ConnectionError("fake connection reset")
        if kind == "http500":
            return FakeResponse(500, text="bad gateway")
        if kind == "ratelimit":
            return self._envelope(10006, "Too many visits", {})
        response = self._route(method, path, params, body)
        if kind == "timeout_after":
            raise requests.Timeout("fake timeout after processing")
        if kind == "http500_after":
            return FakeResponse(500, text="bad gateway")
        return response

    @staticmethod
    def _envelope(code, msg, result):
        return FakeResponse(200, {"retCode": code, "retMsg": msg, "result": result})

    def _route(self, method, path, params, body):
        def ok(result):
            return self._envelope(0, "OK", result)

        if path == "/v5/market/instruments-info":
            item = self.instruments.get(params.get("symbol"))
            return ok({"list": [item] if item else []})
        if path == "/v5/market/tickers":
            mark = self.marks.get(params.get("symbol"))
            return ok({"list": [{"symbol": params.get("symbol"), "markPrice": str(mark)}] if mark else []})
        if path == "/v5/position/list":
            size = self.page_size or int(params.get("limit", 20))
            start = int(params.get("cursor") or 0)
            chunk = self.positions[start:start + size]
            nxt = start + size
            return ok({"list": chunk, "nextPageCursor": str(nxt) if nxt < len(self.positions) else ""})
        if path in ("/v5/order/realtime", "/v5/order/history"):
            if path == "/v5/order/realtime" and self.realtime_misses:
                return ok({"list": []})
            order = self.orders.get(params.get("orderLinkId"))
            return ok({"list": [order] if order else []})
        if path == "/v5/position/closed-pnl":
            since = int(params.get("startTime") or 0)
            items = [r for r in self.closed_pnl
                     if r["symbol"] == params.get("symbol") and int(r["updatedTime"]) >= since]
            return ok({"list": items})
        if path == "/v5/order/create":
            link = body["orderLinkId"]
            if link in self.orders:
                return self._envelope(110072, "OrderLinkedID is duplicate", {})
            oid = f"oid-{next(self._ids)}"
            filled = self.create_status == "Filled"
            self.orders[link] = {
                "orderId": oid, "orderLinkId": link, "symbol": body["symbol"], "side": body["side"],
                "orderStatus": self.create_status, "qty": body["qty"],
                "cumExecQty": body["qty"] if filled else "0",
            }
            return ok({"orderId": oid, "orderLinkId": link})
        if path == "/v5/position/set-leverage":
            if self.leverage_error:
                return self._envelope(self.leverage_error, "fake leverage error", {})
            symbol = body["symbol"]
            if self.leverage.get(symbol) == body["buyLeverage"]:
                return self._envelope(110043, "Set leverage has not been modified.", {})
            self.leverage[symbol] = body["buyLeverage"]
            return ok({})
        return self._envelope(10001, f"fake: unknown path {path}", {})


def make_client(fake):
    """Настоящий BybitClient поверх подменной сессии, без пауз между попытками."""
    from exchange.bybit_client import BybitClient
    client = BybitClient(api_key=API_KEY, api_secret=API_SECRET, testnet=True)
    client._session = fake
    client._RETRY_DELAYS = (0, 0, 0)
    return client
