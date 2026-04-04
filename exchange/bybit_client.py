"""
Bybit REST API client — Phase 2.

Отвечает только за HTTP-коммуникацию с биржей:
- Подписание запросов (HMAC-SHA256)
- Retry с экспоненциальным backoff
- Единый разбор ответов

Принципы:
- Только Testnet пока BYBIT_TESTNET=true в .env
- Fail-closed: любая неопределённость → исключение, не None
- Без бизнес-логики (стратегия, риск, размер позиции — не здесь)
"""
import hashlib
import hmac
import json
import time
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# ========== ENDPOINTS ==========

_MAINNET_BASE = "https://api.bybit.com"
_TESTNET_BASE = "https://api-testnet.bybit.com"

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


# ========== CLIENT ==========

class BybitClient:
    """
    Тонкий HTTP-клиент для Bybit V5 API.

    Умеет:
    - Подписывать приватные запросы (HMAC-SHA256, timestamp + recv_window)
    - Делать GET/POST с retry
    - Разбирать стандартный Bybit V5 envelope {"retCode": 0, "result": ...}

    Не умеет (намеренно):
    - Решать, открывать ли позицию
    - Считать размер позиции
    - Знать о RiskCore, DecisionCore и т.д.
    """

    _RECV_WINDOW = 5000   # ms — рекомендовано Bybit
    _MAX_RETRIES = 3
    _RETRY_DELAYS = (1, 2, 4)  # секунды между попытками

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
            testnet = os.environ.get("BYBIT_TESTNET", "true").lower() == "true"
        self._testnet = testnet

        self._base_url = _TESTNET_BASE if testnet else _MAINNET_BASE
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

        self._qty_step_cache: Dict[str, float] = {}

        mode = "TESTNET" if testnet else "MAINNET"
        logger.info("BybitClient initialized [%s]: %s", mode, self._base_url)

    @staticmethod
    def _load_keys_from_db(current_key: str, current_secret: str):
        """
        Try to load API credentials from encrypted DB storage.
        Falls back to current values (possibly empty) on any error.
        Fail-open: missing keys just mean unauthenticated (read-only) mode.
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
        params = {
            "category": "linear",
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }
        data = self._get("/v5/market/kline", params=params, signed=False)
        candles = data.get("list", [])
        return list(reversed(candles))  # Bybit: новые → старые; нам нужно старые → новые

    def get_mark_price(self, symbol: str) -> float:
        """
        Получить текущую mark price символа (публичный endpoint).

        Используется для валидации SL перед размещением ордера:
        - SHORT (Sell): stop_loss должен быть > mark_price
        - LONG (Buy): stop_loss должен быть < mark_price

        Returns 0.0 при ошибке (caller должен обработать).
        """
        try:
            params = {"category": "linear", "symbol": symbol}
            data = self._get("/v5/market/tickers", params=params, signed=False)
            items = data.get("list", [])
            if items:
                return float(items[0].get("markPrice") or 0)
        except Exception as e:
            logger.warning("get_mark_price(%s) failed: %s", symbol, e)
        return 0.0

    def get_instruments_info(self, symbol: str) -> Dict:
        """Получить параметры инструмента (min qty, step, tick size и т.д.)."""
        params = {"category": "linear", "symbol": symbol}
        return self._get("/v5/market/instruments-info", params=params, signed=False)

    def get_contract_status(self, symbol: str) -> str:
        """Возвращает статус контракта: 'Trading', 'Closed', 'PreLaunch' и т.д."""
        data = self.get_instruments_info(symbol)
        items = data.get("list", [])
        if items:
            return items[0].get("status", "")
        return ""

    def get_qty_step(self, symbol: str) -> float:
        """
        Получить qtyStep для символа (с кэшированием).

        qtyStep — минимальный шаг количества контрактов на Bybit.
        Ордер с qty не кратным qtyStep будет отклонён биржей.
        """
        if symbol not in self._qty_step_cache:
            data = self.get_instruments_info(symbol)
            instruments = data.get("list", [])
            if instruments:
                lot_filter = instruments[0].get("lotSizeFilter", {})
                self._qty_step_cache[symbol] = float(lot_filter.get("qtyStep", "0.001"))
            else:
                logger.warning("No instruments info for %s, using default qtyStep=0.001", symbol)
                self._qty_step_cache[symbol] = 0.001
        return self._qty_step_cache[symbol]

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
        # `val or 0` превращает "" и None в 0 перед float().
        # available_balance = walletBalance (баланс для торговли).
        # availableToWithdraw — это другое: сколько можно вывести с биржи,
        # может быть 0 даже при наличии средств (margin requirements).
        return BalanceInfo(
            total_equity=float(entry.get("equity") or 0),
            available_balance=float(entry.get("walletBalance") or 0),
            wallet_balance=float(entry.get("walletBalance") or 0),
            coin=coin,
        )

    def get_positions(self, symbol: Optional[str] = None) -> list[PositionInfo]:
        """Получить открытые позиции. symbol=None → все позиции."""
        params: Dict[str, Any] = {"category": "linear", "settleCoin": "USDT"}
        if symbol:
            params["symbol"] = symbol
        data = self._get("/v5/position/list", params=params, signed=True)
        result = []
        for p in data.get("list", []):
            result.append(PositionInfo(
                symbol=p["symbol"],
                side=p.get("side", "None"),
                size=float(p.get("size", 0)),
                entry_price=float(p.get("avgPrice", 0)),
                unrealised_pnl=float(p.get("unrealisedPnl", 0)),
                leverage=float(p.get("leverage", 1)),
                stop_loss=float(p["stopLoss"]) if p.get("stopLoss") else None,
                take_profit=float(p["takeProfit"]) if p.get("takeProfit") else None,
            ))
        return result

    def get_open_orders(self, symbol: str) -> list:
        """Получить открытые ордера по символу."""
        data = self._get(
            "/v5/order/realtime",
            params={"category": "linear", "symbol": symbol},
            signed=True,
        )
        return data.get("list", [])

    # ------------------------------------------------------------------ #
    #  Order management (требуют подписи)                                 #
    # ------------------------------------------------------------------ #

    def place_order(
        self,
        symbol: str,
        side: str,               # "Buy" | "Sell"
        qty: float,
        order_type: str = "Market",
        price: Optional[float] = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        time_in_force: str = "GTC",
        reduce_only: bool = False,
        client_order_id: Optional[str] = None,
    ) -> OrderResult:
        """
        Разместить ордер.

        Args:
            symbol: Торговая пара ("BTCUSDT")
            side: "Buy" для LONG, "Sell" для SHORT
            qty: Количество контрактов
            order_type: "Market" | "Limit"
            price: Цена для Limit ордера
            stop_loss: Цена SL (опционально)
            take_profit: Цена TP (опционально)
            time_in_force: "GTC" | "IOC" | "FOK" | "PostOnly"
            reduce_only: True для закрытия позиции
            client_order_id: Пользовательский ID ордера (опционально)
        """
        body: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "side": side,
            "orderType": order_type,
            "qty": str(qty),
            "timeInForce": time_in_force,
        }
        if price is not None:
            body["price"] = str(price)
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
        if reduce_only:
            body["reduceOnly"] = True
        # Always set orderLinkId for idempotency — prevents duplicate orders on retry
        body["orderLinkId"] = client_order_id or f"mb-{uuid.uuid4().hex[:16]}"

        data = self._post("/v5/order/create", body=body, signed=True)
        # Bybit V5 /v5/order/create возвращает только orderId и orderLinkId.
        # orderStatus отсутствует в ответе — для актуального статуса нужен /v5/order/realtime.
        return OrderResult(
            order_id=data.get("orderId", ""),
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=price,
            status="Created",
            time_in_force=time_in_force,
        )

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Отменить ордер. Возвращает True при успехе."""
        body = {
            "category": "linear",
            "symbol": symbol,
            "orderId": order_id,
        }
        self._post("/v5/order/cancel", body=body, signed=True)
        return True

    def set_trading_stop(
        self,
        symbol: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        position_idx: int = 0,
    ) -> bool:
        """Установить/изменить SL и/или TP для открытой позиции."""
        body: Dict[str, Any] = {
            "category": "linear",
            "symbol": symbol,
            "positionIdx": position_idx,
        }
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
        self._post("/v5/position/trading-stop", body=body, signed=True)
        return True

    # ------------------------------------------------------------------ #
    #  Internal HTTP helpers                                              #
    # ------------------------------------------------------------------ #

    def _get(self, path: str, params: Dict, signed: bool) -> Dict:
        url = self._base_url + path
        if signed:
            # Строим query string один раз и используем его в URL напрямую,
            # чтобы подпись совпадала с тем, что реально отправляет requests.
            query_string = urlencode(sorted(params.items()))
            headers = self._build_auth_headers(query_string)
            request_url = f"{url}?{query_string}"
            request_params = None
        else:
            headers = {}
            request_url = url
            request_params = params
        for attempt, delay in enumerate((*self._RETRY_DELAYS, None), start=1):
            try:
                resp = self._session.get(request_url, params=request_params, headers=headers, timeout=10)
                return self._parse_response(resp, path)
            except _RetryableError as e:
                if delay is None:
                    raise RuntimeError(f"GET {path} failed after {self._MAX_RETRIES} retries: {e}") from e
                logger.warning("GET %s attempt %s failed: %s. Retrying in %ss...", path, attempt, e, delay)
                time.sleep(delay)

    def _post(self, path: str, body: Dict, signed: bool) -> Dict:
        url = self._base_url + path
        if signed:
            body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            headers = self._build_auth_headers(body_str)
        else:
            body_str = json.dumps(body)
            headers = {}
        for attempt, delay in enumerate((*self._RETRY_DELAYS, None), start=1):
            try:
                resp = self._session.post(url, data=body_str, headers=headers, timeout=10)
                return self._parse_response(resp, path)
            except _RetryableError as e:
                if delay is None:
                    raise RuntimeError(f"POST {path} failed after {self._MAX_RETRIES} retries: {e}") from e
                logger.warning("POST %s attempt %s failed: %s. Retrying in %ss...", path, attempt, e, delay)
                time.sleep(delay)

    def _build_auth_headers(self, payload_str: str) -> Dict:
        """Построить auth-заголовки для Bybit V5."""
        ts = str(int(time.time() * 1000))
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
    def _parse_response(resp: requests.Response, path: str) -> Dict:
        """Разобрать Bybit V5 envelope. Исключение при любой ошибке."""
        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            body = resp.text[:500]
            # 5xx — временная ошибка сервера, имеет смысл повторить.
            # 4xx — ошибка клиента (плохой запрос, неверная авторизация): повтор бессмысленен.
            if resp.status_code >= 500:
                raise _RetryableError(f"HTTP {resp.status_code} for {path}: {body}") from e
            raise BybitAPIError(resp.status_code, body, path) from e

        try:
            data = resp.json()
        except ValueError as e:
            raise RuntimeError(f"Non-JSON response from {path}: {resp.text[:200]}") from e

        ret_code = data.get("retCode", -1)
        ret_msg = data.get("retMsg", "")

        if ret_code != 0:
            # 10006 = rate limit — retryable
            if ret_code == 10006:
                raise _RetryableError(f"Rate limit (10006) on {path}: {ret_msg}")
            raise BybitAPIError(ret_code, ret_msg, path)

        return data.get("result", {})


# ========== EXCEPTIONS ==========

class BybitAPIError(Exception):
    """Ошибка Bybit API с кодом и сообщением."""
    def __init__(self, code: int, message: str, path: str):
        super().__init__(f"Bybit API error {code} on {path}: {message}")
        self.code = code
        self.message = message
        self.path = path


class _RetryableError(Exception):
    """Внутренний сигнал для retry (rate limit, временная ошибка сети)."""


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
