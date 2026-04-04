"""
Order Executor — Phase 2.

Принимает торговое намерение (символ, направление, размер, SL/TP)
и размещает ордер через BybitClient.

Принципы:
- Fail-closed: любая ошибка → исключение или OrderFailed, не молчаливое None
- DRY_RUN=true → ордер симулируется (лог), биржа не вызывается
- Только Testnet пока BYBIT_TESTNET=true
- Не знает о стратегии, индикаторах, сигналах
"""
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

from exchange.bybit_client import BybitClient, OrderResult, BybitAPIError, get_bybit_client

logger = logging.getLogger(__name__)


# ========== CONFIG ==========

def _is_dry_run() -> bool:
    return os.environ.get("DRY_RUN", "true").lower() == "true"


# ========== DATA TYPES ==========

@dataclass
class TradeRequest:
    """Торговое намерение — вход в позицию."""
    symbol: str
    side: str            # "LONG" | "SHORT"
    qty: float           # Количество контрактов
    entry_price: Optional[float]   # None → Market ордер
    stop_loss: float
    take_profit: Optional[float] = None
    client_order_id: Optional[str] = None


@dataclass
class TradeResult:
    """Результат исполнения торгового намерения."""
    success: bool
    order_id: Optional[str]
    symbol: str
    side: str
    qty: float
    entry_price: Optional[float]
    stop_loss: float
    take_profit: Optional[float]
    dry_run: bool
    error: Optional[str] = None


# ========== EXECUTOR ==========

class OrderExecutor:
    """
    Размещает ордера через BybitClient.

    DRY_RUN=true: логирует намерение, не обращается к бирже.
    DRY_RUN=false + BYBIT_TESTNET=true: реальные ордера на Testnet.
    """

    def __init__(self, client: Optional[BybitClient] = None):
        self._client = client or get_bybit_client()
        self._dry_run = _is_dry_run()
        mode = "DRY_RUN" if self._dry_run else ("TESTNET" if self._client._testnet else "LIVE")
        logger.info("OrderExecutor initialized [%s]", mode)

    def execute(self, request: TradeRequest) -> TradeResult:
        """
        Исполнить торговое намерение.

        Возвращает TradeResult.success=False вместо исключения
        только для ожидаемых ошибок API (недостаточный баланс, etc.).
        Неожиданные ошибки поднимаются наверх.
        """
        if self._dry_run:
            return self._dry_run_execute(request)
        return self._live_execute(request)

    def close_position(self, symbol: str, side: str, qty: float) -> TradeResult:
        """
        Закрыть позицию рыночным ордером (reduce_only=True).

        side: сторона ТЕКУЩЕЙ позиции ("LONG" | "SHORT")
        """
        close_side = "Sell" if side == "LONG" else "Buy"
        request = TradeRequest(
            symbol=symbol,
            side=side,
            qty=qty,
            entry_price=None,
            stop_loss=0.0,
            take_profit=None,
        )
        if self._dry_run:
            logger.info("[DRY_RUN] Close position: %s %s qty=%s", symbol, close_side, qty)
            return TradeResult(
                success=True, order_id="dry_close",
                symbol=symbol, side=side, qty=qty,
                entry_price=None, stop_loss=0.0, take_profit=None, dry_run=True,
            )
        try:
            result = self._client.place_order(
                symbol=symbol,
                side=close_side,
                qty=qty,
                order_type="Market",
                reduce_only=True,
            )
            logger.info("Position closed: %s %s qty=%s order_id=%s", symbol, close_side, qty, result.order_id)
            return TradeResult(
                success=True, order_id=result.order_id,
                symbol=symbol, side=side, qty=qty,
                entry_price=None, stop_loss=0.0, take_profit=None, dry_run=False,
            )
        except BybitAPIError as e:
            logger.error("Failed to close position %s: %s", symbol, e)
            return TradeResult(
                success=False, order_id=None,
                symbol=symbol, side=side, qty=qty,
                entry_price=None, stop_loss=0.0, take_profit=None, dry_run=False,
                error=str(e),
            )

    # ------------------------------------------------------------------ #
    #  Internal                                                           #
    # ------------------------------------------------------------------ #

    def _dry_run_execute(self, request: TradeRequest) -> TradeResult:
        order_type = "Limit" if request.entry_price else "Market"
        logger.info(
            "[DRY_RUN] Would place %s %s order: %s qty=%s entry=%s sl=%s tp=%s",
            order_type, request.side, request.symbol, request.qty,
            request.entry_price, request.stop_loss, request.take_profit,
        )
        fake_order_id = f"dry_{request.symbol}_{int(time.time())}"
        return TradeResult(
            success=True,
            order_id=fake_order_id,
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            entry_price=request.entry_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            dry_run=True,
        )

    def _validate_and_fix_sl(self, request: TradeRequest) -> TradeRequest:
        """
        Валидирует SL против текущей mark price и корректирует при необходимости.

        Bybit error 10001: "StopLoss set for Sell position should greater base_price"
        Для Market ордера base_price = mark price в момент подачи.

        - SHORT (Sell): SL должен быть > mark_price
        - LONG (Buy): SL должен быть < mark_price

        Если SL неверный — добавляем буфер 0.5%.
        """
        if not request.stop_loss:
            return request

        try:
            mark_price = self._client.get_mark_price(request.symbol)
        except Exception:
            return request  # не можем получить цену — пропускаем валидацию

        if mark_price <= 0:
            return request

        sl = request.stop_loss
        symbol = request.symbol

        if request.side == "SHORT":
            # SL должен быть строго выше mark_price
            if sl <= mark_price:
                adjusted = mark_price * 1.005
                logger.warning(
                    "[SL-FIX] SHORT %s: sl=%.4f <= mark=%.4f, adjusting to %.4f (+0.5%%)",
                    symbol, sl, mark_price, adjusted,
                )
                from dataclasses import replace
                return replace(request, stop_loss=adjusted)
        elif request.side == "LONG":
            # SL должен быть строго ниже mark_price
            if sl >= mark_price:
                adjusted = mark_price * 0.995
                logger.warning(
                    "[SL-FIX] LONG %s: sl=%.4f >= mark=%.4f, adjusting to %.4f (-0.5%%)",
                    symbol, sl, mark_price, adjusted,
                )
                from dataclasses import replace
                return replace(request, stop_loss=adjusted)

        return request

    def _live_execute(self, request: TradeRequest) -> TradeResult:
        request = self._validate_and_fix_sl(request)
        bybit_side = "Buy" if request.side == "LONG" else "Sell"
        order_type = "Limit" if request.entry_price else "Market"
        try:
            result: OrderResult = self._client.place_order(
                symbol=request.symbol,
                side=bybit_side,
                qty=request.qty,
                order_type=order_type,
                price=request.entry_price,
                stop_loss=request.stop_loss if request.stop_loss else None,
                take_profit=request.take_profit,
                client_order_id=request.client_order_id,
            )
            logger.info(
                "Order placed: %s %s %s qty=%s order_id=%s",
                request.symbol, bybit_side, order_type, request.qty, result.order_id,
            )
            return TradeResult(
                success=True,
                order_id=result.order_id,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                entry_price=request.entry_price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                dry_run=False,
            )
        except BybitAPIError as e:
            # Ожидаемые ошибки API (недостаточный баланс, неверный qty)
            logger.error("OrderExecutor API error for %s: %s", request.symbol, e)
            return TradeResult(
                success=False,
                order_id=None,
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                entry_price=request.entry_price,
                stop_loss=request.stop_loss,
                take_profit=request.take_profit,
                dry_run=False,
                error=str(e),
            )


# ========== SINGLETON ==========

_executor: Optional[OrderExecutor] = None


def get_order_executor() -> OrderExecutor:
    global _executor
    if _executor is None:
        _executor = OrderExecutor()
    return _executor
