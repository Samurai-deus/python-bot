"""
Order Executor — Phase 2.

Принимает торговое намерение (символ, направление, размер, SL/TP, плечо)
и размещает ордер через BybitClient.

Принципы:
- Fail-closed: любая неопределённость → отказ с причиной, не догадка
- DRY_RUN / PAPER → ордер не отправляется
- Не знает о стратегии, индикаторах, сигналах

Что проверяется перед реальным ордером (переработано 10.09.2026):
- предохранитель — до первого обращения к бирже;
- фильтры инструмента без значений по умолчанию; количество — к шагу лота через
  Decimal (раньше второе округление во float отправляло 0,3 как 0,2, а при шаге
  0,25 могло округлить вверх); цены — к шагу цены;
- стоп по правильную сторону от текущей цены — иначе сигнал устарел и ордер не
  отправляется (раньше стоп переставлялся на ±0,5 % от цены);
- минимальный номинал;
- плечо: задано, не выше биржевого, ликвидация не раньше стопа; выставляется на
  бирже до ордера (раньше не отправлялось никогда).
Если связь оборвалась при отправке, ордер сверяется по orderLinkId: найден —
успех, не найден — «состояние неизвестно», а не молчаливый провал.
"""
import logging
import time
from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Callable, Dict, Optional, Tuple

from exchange.bybit_client import (
    BybitAPIError,
    BybitClient,
    BybitUnavailable,
    InstrumentFilters,
    OrderResult,
    OrderStateUnknown,
    get_bybit_client,
    new_order_link_id,
)

logger = logging.getLogger(__name__)

RECONCILE_ATTEMPTS = 3
RECONCILE_DELAY_SECONDS = 1.0
# Какую долю хода до ликвидации может занимать стоп. Ликвидация наступает примерно
# при движении 1/плечо за вычетом поддерживающей маржи; стоп дальше 80 % этого
# расстояния может не успеть сработать.
LIQUIDATION_BUFFER = Decimal("0.8")
# Статусы, при которых ордер на бирже жив или исполняется.
_LIVE_STATUSES = {"New", "PartiallyFilled", "Untriggered", "Triggered"}


# ========== CONFIG ==========

def _is_dry_run() -> bool:
    """Ордер уходит на биржу только в режимах TESTNET и LIVE (trading_mode)."""
    from trading_mode import sends_real_orders
    return not sends_real_orders()


# ========== DATA TYPES ==========

@dataclass
class TradeRequest:
    """Торговое намерение — вход в позицию."""
    symbol: str
    side: str            # "LONG" | "SHORT"
    qty: float           # Количество контрактов (сырое: к шагу приводит исполнитель)
    entry_price: Optional[float]   # None → Market ордер
    stop_loss: float
    take_profit: Optional[float] = None
    client_order_id: Optional[str] = None
    leverage: Optional[float] = None


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
    order_link_id: Optional[str] = None
    state_unknown: bool = False


# ========== ROUNDING ==========

def round_qty(qty, filters: InstrumentFilters) -> Tuple[Decimal, Optional[str]]:
    """Количество вниз к шагу лота; ошибка — если вне пределов инструмента."""
    try:
        q = Decimal(str(qty))
    except Exception:
        return Decimal(0), f"qty не число: {qty!r}"
    if q <= 0:
        return Decimal(0), f"qty must be positive, got {qty}"
    step = filters.qty_step
    q = ((q / step).to_integral_value(rounding=ROUND_DOWN) * step).quantize(step)
    if q < filters.min_qty:
        return Decimal(0), f"{filters.symbol}: qty {q} below minimum {filters.min_qty}"
    if q > filters.max_market_qty:
        return Decimal(0), f"{filters.symbol}: qty {q} exceeds maximum {filters.max_market_qty}"
    return q, None


def round_price(price, filters: InstrumentFilters) -> Decimal:
    """Цена к ближайшему шагу цены."""
    tick = filters.tick_size
    return ((Decimal(str(price)) / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick).quantize(tick)


# ========== EXECUTOR ==========

class OrderExecutor:
    """Размещает ордера через BybitClient."""

    def __init__(self, client: Optional[BybitClient] = None, sleep: Callable[[float], None] = time.sleep):
        self._client = client or get_bybit_client()
        self._dry_run = _is_dry_run()
        self._sleep = sleep
        self._leverage_set: Dict[str, Decimal] = {}
        mode = "DRY_RUN" if self._dry_run else ("TESTNET" if self._client._testnet else "LIVE")
        logger.info("OrderExecutor initialized [%s]", mode)

    # ------------------------------------------------------------------ #

    def _fail(self, request: TradeRequest, error: str, *, qty=None,
              link_id: Optional[str] = None, state_unknown: bool = False) -> TradeResult:
        return TradeResult(
            success=False, order_id=None,
            symbol=request.symbol, side=request.side,
            qty=float(qty) if qty is not None else request.qty,
            entry_price=request.entry_price, stop_loss=request.stop_loss,
            take_profit=request.take_profit, dry_run=self._dry_run,
            error=error, order_link_id=link_id, state_unknown=state_unknown,
        )

    def _ok(self, request: TradeRequest, order_id: str, qty: Decimal, sl: Optional[Decimal],
            tp: Optional[Decimal], link_id: Optional[str]) -> TradeResult:
        return TradeResult(
            success=True, order_id=order_id,
            symbol=request.symbol, side=request.side, qty=float(qty),
            entry_price=request.entry_price,
            stop_loss=float(sl) if sl is not None else 0.0,
            take_profit=float(tp) if tp is not None else None,
            dry_run=False, order_link_id=link_id,
        )

    def _validate_qty(self, symbol: str, qty: float) -> Tuple[float, Optional[str]]:
        """Количество, приведённое к фильтрам инструмента, и ошибка (или None)."""
        try:
            filters = self._client.get_instrument_filters(symbol)
        except Exception as e:
            return 0.0, f"фильтры инструмента {symbol} недоступны: {e}"
        q, error = round_qty(qty, filters)
        return float(q), error

    # ------------------------------------------------------------------ #

    def execute(self, request: TradeRequest) -> TradeResult:
        """
        Исполнить торговое намерение. Ожидаемые отказы — TradeResult.success=False
        с причиной; неожиданные ошибки поднимаются наверх.
        """
        # Второй рубеж предохранителя — только для реальных ордеров, до первого
        # обращения к бирже. close_position() намеренно НЕ проверяет: закрытие снижает риск.
        if not self._dry_run:
            from execution.kill_switch import trading_halt_reason
            halt_reason = trading_halt_reason()
            if halt_reason:
                logger.error("OrderExecutor: ордер %s %s не отправлен — %s", request.side, request.symbol, halt_reason)
                return self._fail(request, f"Trading halted: {halt_reason}")

        try:
            filters = self._client.get_instrument_filters(request.symbol)
        except Exception as e:
            logger.error("OrderExecutor: фильтры инструмента %s недоступны: %s", request.symbol, e)
            return self._fail(request, f"Instrument filters unavailable: {e}")
        if filters.status and filters.status != "Trading":
            return self._fail(request, f"{request.symbol} не торгуется на бирже (status={filters.status})")

        qty, error = round_qty(request.qty, filters)
        if error:
            logger.error("Position size validation failed: %s", error)
            return self._fail(request, f"Position size invalid: {error}")
        if float(qty) != request.qty:
            logger.info("Qty adjusted for %s: %s → %s (step alignment)", request.symbol, request.qty, qty)

        if self._dry_run:
            return self._dry_run_execute(replace(request, qty=float(qty)))
        return self._live_execute(request, filters, qty)

    def close_position(self, symbol: str, side: str, qty: float) -> TradeResult:
        """
        Закрыть позицию рыночным ордером (reduce_only=True).
        side: сторона ТЕКУЩЕЙ позиции ("LONG" | "SHORT").
        """
        request = TradeRequest(symbol=symbol, side=side, qty=qty, entry_price=None, stop_loss=0.0)
        try:
            filters = self._client.get_instrument_filters(symbol)
        except Exception as e:
            return self._fail(request, f"Instrument filters unavailable: {e}")
        q, error = round_qty(qty, filters)
        if error:
            logger.error("Close position size validation failed: %s", error)
            return self._fail(request, f"Position size invalid: {error}")

        close_side = "Sell" if side == "LONG" else "Buy"
        if self._dry_run:
            logger.info("[DRY_RUN] Close position: %s %s qty=%s", symbol, close_side, q)
            return TradeResult(success=True, order_id="dry_close", symbol=symbol, side=side, qty=float(q),
                               entry_price=None, stop_loss=0.0, take_profit=None, dry_run=True)

        link_id = new_order_link_id()
        try:
            result = self._client.place_order(symbol=symbol, side=close_side, qty=q, order_type="Market",
                                              reduce_only=True, client_order_id=link_id)
        except OrderStateUnknown as e:
            logger.error("[EXECUTOR] %s: связь при закрытии оборвалась (%s) — сверяю %s", symbol, e.reason, link_id)
            return self._reconcile(request, q, None, None, link_id)
        except (BybitAPIError, BybitUnavailable) as e:
            logger.error("Failed to close position %s: %s", symbol, e)
            return self._fail(request, str(e), qty=q, link_id=link_id)
        logger.info("Position closed: %s %s qty=%s order_id=%s", symbol, close_side, q, result.order_id)
        return self._ok(request, result.order_id, q, None, None, link_id)

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
        return TradeResult(
            success=True, order_id=f"dry_{request.symbol}_{int(time.time())}",
            symbol=request.symbol, side=request.side, qty=request.qty,
            entry_price=request.entry_price, stop_loss=request.stop_loss,
            take_profit=request.take_profit, dry_run=True,
        )

    def _prepare_leverage(self, request: TradeRequest, filters: InstrumentFilters, mark: Decimal) -> Optional[str]:
        """Проверить и выставить плечо. Причина отказа — или None."""
        lev = request.leverage
        if not lev or lev <= 0:
            return "плечо не задано — биржа взяла бы то, что стоит на счёте"
        lev_d = Decimal(str(lev))
        if filters.max_leverage is not None and lev_d > filters.max_leverage:
            return f"плечо {lev_d} больше максимума биржи {filters.max_leverage}"
        stop_distance = abs(mark - Decimal(str(request.stop_loss))) / mark
        if stop_distance * lev_d >= LIQUIDATION_BUFFER:
            return (f"при плече {lev_d} ликвидация наступит раньше стопа "
                    f"(стоп в {stop_distance * 100:.2f} % от цены)")
        if self._leverage_set.get(request.symbol) == lev_d:
            return None
        try:
            self._client.set_leverage(request.symbol, lev_d)
        except Exception as e:
            return f"не удалось выставить плечо {lev_d}: {e}"
        self._leverage_set[request.symbol] = lev_d
        return None

    def _live_execute(self, request: TradeRequest, filters: InstrumentFilters, qty: Decimal) -> TradeResult:
        symbol = request.symbol
        mark = self._client.get_mark_price(symbol)
        if not mark or mark <= 0:
            return self._fail(request, "нет mark price — не проверить стоп и номинал, ордер не отправлен", qty=qty)
        mark_d = Decimal(str(mark))

        stop = request.stop_loss
        if not stop:
            return self._fail(request, "ордер без стопа не отправляется", qty=qty)
        if (request.side == "LONG" and stop >= mark) or (request.side == "SHORT" and stop <= mark):
            logger.warning("[EXECUTOR] %s %s: сигнал устарел — цена %s уже за стопом %s", symbol, request.side, mark, stop)
            return self._fail(request, f"сигнал устарел: цена {mark} уже за стопом {stop}", qty=qty)

        if qty * mark_d < filters.min_notional:
            return self._fail(request, f"номинал {qty * mark_d:.2f} ниже минимума биржи {filters.min_notional}", qty=qty)

        lev_error = self._prepare_leverage(request, filters, mark_d)
        if lev_error:
            logger.error("[EXECUTOR] %s: %s", symbol, lev_error)
            return self._fail(request, lev_error, qty=qty)

        sl = round_price(stop, filters)
        tp = round_price(request.take_profit, filters) if request.take_profit else None
        price = round_price(request.entry_price, filters) if request.entry_price else None
        bybit_side = "Buy" if request.side == "LONG" else "Sell"
        order_type = "Limit" if price is not None else "Market"
        link_id = request.client_order_id or new_order_link_id()

        try:
            result: OrderResult = self._client.place_order(
                symbol=symbol, side=bybit_side, qty=qty, order_type=order_type, price=price,
                stop_loss=sl, take_profit=tp, client_order_id=link_id,
            )
        except OrderStateUnknown as e:
            logger.error("[EXECUTOR] %s: связь при отправке ордера оборвалась (%s) — сверяю по orderLinkId %s",
                         symbol, e.reason, link_id)
            return self._reconcile(request, qty, sl, tp, link_id)
        except (BybitAPIError, BybitUnavailable) as e:
            logger.error("OrderExecutor API error for %s: %s", symbol, e)
            return self._fail(request, str(e), qty=qty, link_id=link_id)

        logger.info("Order placed: %s %s %s qty=%s order_id=%s link=%s",
                    symbol, bybit_side, order_type, qty, result.order_id, link_id)
        return self._ok(request, result.order_id, qty, sl, tp, link_id)

    def _reconcile(self, request: TradeRequest, qty: Decimal, sl: Optional[Decimal],
                   tp: Optional[Decimal], link_id: str) -> TradeResult:
        """После неоднозначного сбоя: найти ордер по orderLinkId и решить по факту."""
        last_error: Optional[Exception] = None
        for attempt in range(1, RECONCILE_ATTEMPTS + 1):
            try:
                found = self._client.find_order(request.symbol, link_id)
            except Exception as e:
                found, last_error = None, e
            if found:
                executed = Decimal(str(found.get("cumExecQty") or "0"))
                status = found.get("orderStatus", "")
                if executed > 0 or status in _LIVE_STATUSES:
                    logger.warning("[EXECUTOR] %s: ордер %s найден на бирже после сбоя (status=%s, исполнено %s)",
                                   request.symbol, link_id, status, executed)
                    return self._ok(request, found.get("orderId", ""), executed if executed > 0 else qty, sl, tp, link_id)
                return self._fail(request, f"ордер {link_id} не исполнен биржей (status={status})",
                                  qty=qty, link_id=link_id)
            if attempt < RECONCILE_ATTEMPTS:
                self._sleep(RECONCILE_DELAY_SECONDS)
        detail = f" ({last_error})" if last_error else ""
        return self._fail(
            request,
            f"связь оборвалась при отправке ордера {link_id}, найти его на бирже не удалось{detail} — "
            f"проверьте позицию вручную",
            qty=qty, link_id=link_id, state_unknown=True,
        )


# ========== SINGLETON ==========

_executor: Optional[OrderExecutor] = None


def get_order_executor() -> OrderExecutor:
    global _executor
    if _executor is None:
        _executor = OrderExecutor()
    return _executor
