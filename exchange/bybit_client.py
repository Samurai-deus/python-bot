"""
Bybit REST API client — Phase 2.

Отвечает только за HTTP-коммуникацию с биржей:
- Подписание запросов (HMAC-SHA256)
- Повторы с разными правилами для чтения, идемпотентной записи и создания ордера
- Единый разбор ответов

Принципы:
- Fail-closed: любая неопределённость → исключение, не None
- Без бизнес-логики (стратегия, риск, размер позиции — не здесь)

Повторы (переработано 10.09.2026, аудит торгового пути):
- Подпись и метка времени строятся заново на каждой попытке. Раньше их строили
  один раз до цикла, и попытка через 7 с уходила с меткой вне recv_window (5 с).
- Чтение (GET) повторяется при 5xx, лимите запросов (10006), таймауте и обрыве
  соединения. Раньше таймаут и обрыв не повторялись вовсе.
- Создание ордера вслепую НЕ повторяется. При 5xx, таймауте, обрыве, мусорном
  ответе ордер мог быть принят — это OrderStateUnknown, и исполнитель сверяет
  его по orderLinkId. Дубль orderLinkId (110072) значит «биржа уже видела этот
  ордер» — тоже OrderStateUnknown. Повторяется только отказ по лимиту запросов:
  такой запрос биржа точно не приняла.
"""
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ========== ENDPOINTS ==========

_MAINNET_BASE = "https://api.bybit.com"
_TESTNET_BASE = "https://api-testnet.bybit.com"

Number = Union[Decimal, float, int, str]

# orderLinkId: до 36 символов, буквы, цифры, дефис и подчёркивание (документация v5).
_ORDER_LINK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,36}$")


def fmt_number(value: Number) -> str:
    """
    Число для API Bybit: без экспоненты. str(float) даёт '1e-05', и биржа такой
    ордер не примет. Хвосты вида 0.21359999999999998 устраняет округление к шагу
    в исполнителе — сюда приходит уже Decimal.
    """
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def new_order_link_id() -> str:
    """Уникальный orderLinkId: миллисекунды + случайный хвост, 23 символа."""
    return f"mb-{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"


# ========== RESPONSE TYPES ==========

@dataclass
class OrderResult:
    """Результат размещения/отмены ордера."""
    order_id: str
    symbol: str
    side: str            # "Buy" | "Sell"
    order_type: str      # "Market" | "Limit"
    qty: float
    price: Optional[float]
    status: str          # "Created" | "Filled" | "Cancelled" etc.
    time_in_force: str
    order_link_id: str = ""


@dataclass
class PositionInfo:
    """Текущая позиция по символу."""
    symbol: str
    side: str            # "Buy" | "Sell" | "None"
    size: float          # Количество контрактов
    entry_price: float
    unrealised_pnl: float
    leverage: float
    stop_loss: Optional[float]
    take_profit: Optional[float]


@dataclass
class BalanceInfo:
    """Баланс кошелька."""
    total_equity: float
    available_balance: float
    wallet_balance: float
    coin: str            # "USDT"


@dataclass(frozen=True)
class InstrumentFilters:
    """Фильтры инструмента — единственный источник шагов и лимитов для ордера."""
    symbol: str
    status: str
    tick_size: Decimal
    qty_step: Decimal
    min_qty: Decimal
    max_market_qty: Decimal
    min_notional: Decimal
    max_leverage: Optional[Decimal]


# ========== CLIENT ==========

class BybitClient:
    """
    Тонкий HTTP-клиент для Bybit V5 API.

    Не умеет (намеренно): решать, открывать ли позицию, считать размер,
    знать о RiskCore и DecisionCore.
    """

    _RECV_WINDOW = 5000   # ms — рекомендовано Bybit
    _RETRY_DELAYS = (1, 2, 4)  # секунды между попытками
    _FILTERS_TTL = 6 * 3600
    _MAX_POSITION_PAGES = 50

    ORDER_LINK_DUPLICATE = 110072
    LEVERAGE_NOT_MODIFIED = 110043

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: Optional[bool] = None,
    ):
        self._api_key = api_key or os.environ.get("BYBIT_API_KEY", "")
        self._api_secret = api_secret or os.environ.get("BYBIT_API_SECRET", "")

        # If credentials not in env, try encrypted DB storage
        if not self._api_key or not self._api_secret:
            self._api_key, self._api_secret = self._load_keys_from_db(
                self._api_key, self._api_secret
            )

        if testnet is None:
            # Хост берётся из того же резолвера, что и режим: uses_testnet_endpoint()
            # истинна ровно в режиме TESTNET. Раньше здесь был собственный разбор
            # BYBIT_TESTNET, и при BYBIT_TESTNET=1 ордер уходил на mainnet.
            from trading_mode import uses_testnet_endpoint
            testnet = uses_testnet_endpoint()
        self._testnet = testnet

        self._base_url = _TESTNET_BASE if testnet else _MAINNET_BASE
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        self._filters_cache: Dict[str, tuple] = {}

        mode = "TESTNET" if testnet else "MAINNET"
        logger.info("BybitClient initialized [%s]: %s", mode, self._base_url)

    @staticmethod
    def _load_keys_from_db(current_key: str, current_secret: str):
        """
        Try to load API credentials from encrypted DB storage.
        Falls back to current values (possibly empty) on any error.
        """
        try:
            from database import get_encrypted_api_key
            from utils.crypto import decrypt

            key = current_key
            secret = current_secret

            if not key:
                enc_key = get_encrypted_api_key("BYBIT_API_KEY")
                if enc_key:
                    key = decrypt(enc_key)

            if not secret:
                enc_secret = get_encrypted_api_key("BYBIT_API_SECRET")
                if enc_secret:
                    secret = decrypt(enc_secret)

            return key, secret
        except Exception as e:
            logger.debug("Could not load API keys from DB (will use env): %s", e)
            return current_key, current_secret

    # ------------------------------------------------------------------ #
    #  Public market data (без подписи)                                   #
    # ------------------------------------------------------------------ #

    def get_klines(self, symbol: str, interval: str, limit: int = 120) -> list:
        """Получить свечи (публичный endpoint, без ключей)."""
        params = {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit}
        data = self._get("/v5/market/kline", params=params, signed=False)
        candles = data.get("list", [])
        return list(reversed(candles))  # Bybit: новые → старые; нам нужно старые → новые

    def get_mark_price(self, symbol: str) -> float:
        """Текущая mark price символа. 0.0 при ошибке — вызывающий обязан отказать."""
        try:
            data = self._get("/v5/market/tickers", params={"category": "linear", "symbol": symbol}, signed=False)
            items = data.get("list", [])
            if items:
                return float(items[0].get("markPrice") or 0)
        except Exception as e:
            logger.warning("get_mark_price(%s) failed: %s", symbol, e)
        return 0.0

    def get_instruments_info(self, symbol: str) -> Dict:
        """Сырые параметры инструмента."""
        return self._get("/v5/market/instruments-info", params={"category": "linear", "symbol": symbol}, signed=False)

    def get_instrument_filters(self, symbol: str) -> InstrumentFilters:
        """
        Шаг цены, шаг и пределы количества, минимальный номинал, максимальное плечо.
        Кэш 6 ч; биржа не ответила — устаревшее значение; значения нет вовсе —
        исключение. Никаких значений по умолчанию: раньше исполнитель при ошибке
        молча брал шаг 0.001, и ордер уходил с количеством, которое биржа не примет.
        """
        now = time.time()
        cached = self._filters_cache.get(symbol)
        if cached and now - cached[0] < self._FILTERS_TTL:
            return cached[1]
        try:
            items = self.get_instruments_info(symbol).get("list") or []
            if not items:
                raise RuntimeError(f"нет данных инструмента {symbol}")
            filters = self._parse_filters(symbol, items[0])
        except Exception:
            if cached:
                logger.warning("instrument filters %s: биржа не ответила, беру значение из кэша", symbol)
                return cached[1]
            raise
        self._filters_cache[symbol] = (now, filters)
        return filters

    @staticmethod
    def _parse_filters(symbol: str, item: Dict) -> InstrumentFilters:
        lot = item.get("lotSizeFilter") or {}
        price = item.get("priceFilter") or {}
        lev = item.get("leverageFilter") or {}

        def required(value, name):
            if value in (None, ""):
                raise RuntimeError(f"{symbol}: в instruments-info нет {name}")
            d = Decimal(str(value))
            if d <= 0:
                raise RuntimeError(f"{symbol}: {name}={value} — не положительное")
            return d

        return InstrumentFilters(
            symbol=symbol,
            status=item.get("status", ""),
            tick_size=required(price.get("tickSize"), "priceFilter.tickSize"),
            qty_step=required(lot.get("qtyStep"), "lotSizeFilter.qtyStep"),
            min_qty=required(lot.get("minOrderQty"), "lotSizeFilter.minOrderQty"),
            # Для рыночного ордера предел — maxMktOrderQty; maxOrderQty — для лимитных.
            max_market_qty=required(lot.get("maxMktOrderQty") or lot.get("maxOrderQty"), "lotSizeFilter.maxMktOrderQty"),
            min_notional=Decimal(str(lot.get("minNotionalValue") or "0")),
            max_leverage=Decimal(str(lev["maxLeverage"])) if lev.get("maxLeverage") else None,
        )

    def get_contract_status(self, symbol: str) -> str:
        """Статус контракта: 'Trading', 'Closed', 'PreLaunch' и т.д."""
        items = self.get_instruments_info(symbol).get("list", [])
        return items[0].get("status", "") if items else ""

    def get_qty_step(self, symbol: str) -> float:
        """qtyStep символа (через фильтры инструмента, без значения по умолчанию)."""
        return float(self.get_instrument_filters(symbol).qty_step)

    # ------------------------------------------------------------------ #
    #  Account data (требуют подписи)                                     #
    # ------------------------------------------------------------------ #

    def get_wallet_balance(self, coin: str = "USDT") -> BalanceInfo:
        """Получить баланс Unified Margin счёта."""
        data = self._get(
            "/v5/account/wallet-balance",
            params={"accountType": "UNIFIED", "coin": coin},
            signed=True,
        )
        accounts = data.get("list", [])
        if not accounts:
            raise RuntimeError(f"Empty wallet-balance response for coin={coin}")
        coins = accounts[0].get("coin", [])
        entry = next((c for c in coins if c.get("coin") == coin), None)
        if entry is None:
            raise RuntimeError(f"Coin {coin} not found in wallet-balance response")
        # Bybit может вернуть "" для числовых полей (пустой счёт / testnet).
        return BalanceInfo(
            total_equity=float(entry.get("equity") or 0),
            available_balance=float(entry.get("walletBalance") or 0),
            wallet_balance=float(entry.get("walletBalance") or 0),
            coin=coin,
        )

    def get_positions(self, symbol: Optional[str] = None) -> list:
        """
        Открытые позиции, все страницы. Раньше читалась только первая страница
        (по умолчанию 20 записей), и позиция со второй считалась закрытой.
        """
        params: Dict[str, Any] = {"category": "linear", "settleCoin": "USDT", "limit": 200}
        if symbol:
            params["symbol"] = symbol
        result = []
        cursor = None
        for _ in range(self._MAX_POSITION_PAGES):
            page = dict(params)
            if cursor:
                page["cursor"] = cursor
            data = self._get("/v5/position/list", params=page, signed=True)
            items = data.get("list") or []
            for p in items:
                result.append(PositionInfo(
                    symbol=p["symbol"],
                    side=p.get("side", "None"),
                    size=float(p.get("size") or 0),
                    entry_price=float(p.get("avgPrice") or 0),
                    unrealised_pnl=float(p.get("unrealisedPnl") or 0),
                    leverage=float(p.get("leverage") or 1),
                    stop_loss=float(p["stopLoss"]) if p.get("stopLoss") else None,
                    take_profit=float(p["takeProfit"]) if p.get("takeProfit") else None,
                ))
            cursor = data.get("nextPageCursor")
            if not cursor or not items:
                return result
        raise RuntimeError(f"position/list: больше {self._MAX_POSITION_PAGES} страниц — ответ не сходится")

    def get_closed_pnl(self, symbol: str, start_time_ms: int, limit: int = 50) -> list:
        """Закрытый PnL по символу начиная с start_time_ms (/v5/position/closed-pnl)."""
        data = self._get(
            "/v5/position/closed-pnl",
            params={"category": "linear", "symbol": symbol, "startTime": int(start_time_ms), "limit": limit},
            signed=True,
        )
        return data.get("list") or []

    def get_open_orders(self, symbol: str) -> list:
        """Получить открытые ордера по символу."""
        data = self._get("/v5/order/realtime", params={"category": "linear", "symbol": symbol}, signed=True)
        return data.get("list", [])

    def find_order(self, symbol: str, order_link_id: str) -> Optional[Dict]:
        """
        Ордер по orderLinkId в любом статусе — или None. Сначала order/realtime
        (по orderLinkId отдаёт и исполненные), затем order/history: после
        перезапуска сервера биржи исполненные ордера видны только там.
        """
        params = {"category": "linear", "symbol": symbol, "orderLinkId": order_link_id}
        for path in ("/v5/order/realtime", "/v5/order/history"):
            data = self._get(path, params=params, signed=True)
            for item in data.get("list") or []:
                if item.get("orderLinkId") == order_link_id:
                    return item
        return None

    # ------------------------------------------------------------------ #
    #  Order management (требуют подписи)                                 #
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        symbol: str,
        side: str,               # "Buy" | "Sell"
        qty: Number,
        order_type: str = "Market",
        price: Optional[Number] = None,
        stop_loss: Optional[Number] = None,
        take_profit: Optional[Number] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
        position_idx: int = 0,
    ) -> OrderResult:
        """
        Разместить ордер. Одна попытка (кроме отказа по лимиту запросов).

        Raises:
            OrderStateUnknown: запрос мог дойти до биржи — сверить по orderLinkId.
            BybitAPIError: биржа ордер отклонила.
            BybitUnavailable: биржа отклоняла по лимиту запросов все попытки.
        """
        link_id = client_order_id or new_order_link_id()
        if not _ORDER_LINK_ID_RE.match(link_id):
            raise ValueError(f"orderLinkId {link_id!r}: до 36 символов, буквы, цифры, '-' и '_'")
        body: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": fmt_number(qty),
            "timeInForce": time_in_force,
            # 0 — односторонний режим позиций; явно, а не на усмотрение биржи.
            "positionIdx": position_idx,
            "orderLinkId": link_id,
        }
        if price is not None:
            body["price"] = fmt_number(price)
        if stop_loss is not None:
            body["stopLoss"] = fmt_number(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = fmt_number(take_profit)
        if reduce_only:
            body["reduceOnly"] = True

        try:
            data = self._post("/v5/order/create", body=body, signed=True,
                              retry_network=False, retry_server=False)
        except BybitAPIError as e:
            if e.code == self.ORDER_LINK_DUPLICATE:
                raise OrderStateUnknown(link_id, "биржа уже знает этот orderLinkId") from e
            raise
        except BybitUnavailable:
            raise
        except (_ServerError, requests.RequestException, BybitBadResponse) as e:
            raise OrderStateUnknown(link_id, f"{type(e).__name__}: {e}") from e

        # Ответ order/create — только orderId и orderLinkId; статус — через find_order.
        return OrderResult(
            order_id=data.get("orderId", ""),
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=float(Decimal(str(qty))),
            price=float(Decimal(str(price))) if price is not None else None,
            status="Created",
            time_in_force=time_in_force,
            order_link_id=data.get("orderLinkId") or link_id,
        )

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер. Возвращает True при успехе."""
        self._post("/v5/order/cancel", body={"category": "linear", "symbol": symbol, "orderId": order_id}, signed=True)
        return True

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[Number] = None,
        take_profit: Optional[Number] = None,
        position_idx: int = 0,
    ) -> bool:
        """Установить/изменить SL и/или TP для открытой позиции."""
        body: Dict[str, Any] = {"category": "linear", "symbol": symbol, "positionIdx": position_idx}
        if stop_loss is not None:
            body["stopLoss"] = fmt_number(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = fmt_number(take_profit)
        self._post("/v5/position/trading-stop", body=body, signed=True)
        return True

    def set_leverage(self, symbol: str, leverage: Number) -> None:
        """
        Плечо символа (одинаковое для обеих сторон — односторонний режим).
        «Не изменилось» (110043) — успех. Раньше плечо не отправлялось никогда,
        и биржа брала то, что стояло на счёте.
        """
        lev = fmt_number(leverage)
        try:
            self._post("/v5/position/set-leverage",
                       body={"category": "linear", "symbol": symbol, "buyLeverage": lev, "sellLeverage": lev},
                       signed=True)
        except BybitAPIError as e:
            if e.code == self.LEVERAGE_NOT_MODIFIED:
                return
            raise

    # ------------------------------------------------------------------ #
    #  Internal HTTP helpers                                              #
    # ------------------------------------------------------------------ #

    def _get(self, path: str, params: Dict, signed: bool) -> Dict:
        return self._request("GET", path, params=params, signed=signed,
                             retry_network=True, retry_server=True)

    def _post(self, path: str, body: Dict, signed: bool,
              retry_network: bool = True, retry_server: bool = True) -> Dict:
        return self._request("POST", path, body=body, signed=signed,
                             retry_network=retry_network, retry_server=retry_server)

    def _request(self, method: str, path: str, params: Optional[Dict] = None,
                 body: Optional[Dict] = None, signed: bool = False,
                 retry_network: bool = True, retry_server: bool = True) -> Dict:
        url = self._base_url + path
        if method == "GET":
            # Подписывается ровно та строка запроса, что уходит в URL.
            query = urlencode(sorted((params or {}).items()))
            target = f"{url}?{query}" if query else url
            payload = query
        else:
            payload = json.dumps(body or {}, separators=(",", ":"), ensure_ascii=False)
            target = url

        attempt = 0
        while True:
            attempt += 1
            headers = self._build_auth_headers(payload) if signed else {}
            try:
                if method == "GET":
                    resp = self._session.get(target, headers=headers, timeout=10)
                else:
                    resp = self._session.post(target, data=payload.encode("utf-8"), headers=headers, timeout=10)
                return self._parse_response(resp, path)
            except _RateLimited as e:
                err: Exception = e
            except _ServerError as e:
                if not retry_server:
                    raise
                err = e
            except (requests.Timeout, requests.ConnectionError) as e:
                if not retry_network:
                    raise
                err = e
            if attempt > len(self._RETRY_DELAYS):
                raise BybitUnavailable(f"{method} {path}: {attempt} попыток без ответа: {err}") from err
            delay = self._RETRY_DELAYS[attempt - 1]
            logger.warning("%s %s attempt %s failed: %s. Retrying in %ss...", method, path, attempt, err, delay)
            time.sleep(delay)

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _build_auth_headers(self, payload_str: str) -> Dict:
        """Auth-заголовки Bybit V5: подпись над timestamp + key + recv_window + payload."""
        ts = str(self._now_ms())
        recv_window = str(self._RECV_WINDOW)
        sign_payload = ts + self._api_key + recv_window + payload_str
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            sign_payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": ts,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
        }

    @staticmethod
    def _parse_response(resp, path: str) -> Dict:
        """Разобрать Bybit V5 envelope. Исключение при любой ошибке."""
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            body = resp.text[:500]
            # 5xx — временная ошибка сервера; 4xx — ошибка клиента, повтор бессмысленен.
            if resp.status_code >= 500:
                raise _ServerError(f"HTTP {resp.status_code} for {path}: {body}") from e
            raise BybitAPIError(resp.status_code, body, path) from e

        try:
            data = resp.json()
        except ValueError as e:
            raise BybitBadResponse(f"Non-JSON response from {path}: {resp.text[:200]}") from e

        ret_code = data.get("retCode", -1)
        ret_msg = data.get("retMsg", "")
        if ret_code != 0:
            if ret_code == 10006:
                raise _RateLimited(f"Rate limit (10006) on {path}: {ret_msg}")
            raise BybitAPIError(ret_code, ret_msg, path)
        return data.get("result", {})


# ========== EXCEPTIONS ==========

class BybitAPIError(Exception):
    """Ошибка Bybit API с кодом и сообщением: биржа ответила отказом."""
    def __init__(self, code: int, message: str, path: str):
        super().__init__(f"Bybit API error {code} on {path}: {message}")
        self.code = code
        self.message = message
        self.path = path


class BybitUnavailable(RuntimeError):
    """Все попытки исчерпаны: сеть, 5xx или лимит запросов."""


class BybitBadResponse(RuntimeError):
    """Ответ не разобрать (не JSON)."""


class OrderStateUnknown(Exception):
    """Запрос на создание ордера мог дойти до биржи — нужна сверка по orderLinkId."""
    def __init__(self, order_link_id: str, reason: str):
        super().__init__(f"order {order_link_id}: state unknown — {reason}")
        self.order_link_id = order_link_id
        self.reason = reason


class _RetryableError(Exception):
    """Внутренний сигнал для повтора."""


class _ServerError(_RetryableError):
    """HTTP 5xx."""


class _RateLimited(_RetryableError):
    """retCode 10006: запрос отклонён лимитером, до исполнения не дошёл."""


# ========== SINGLETON ==========

_client: Optional[BybitClient] = None
_client_lock = threading.Lock()


def get_bybit_client() -> BybitClient:
    """Получить глобальный экземпляр клиента (thread-safe singleton)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = BybitClient()
    return _client
