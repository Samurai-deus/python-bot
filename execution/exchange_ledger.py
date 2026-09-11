"""
Единый журнал сделок в режимах с реальными ордерами (TESTNET/LIVE) — задачи 3.6, 3.8, 3.9.

До 10.09.2026 журналов было два. Бумажная сделка в `trades` открывалась и при
реальном ордере, бумажный монитор закрывал её по своим уровням, пока реальная
позиция жила, а Risk Core, серия убытков и просадка читали только бумажный
журнал. Проверка «уже открыто» шла после отправки ордера, трекер позиций по
символу молча перезаписывал прежнюю, а при старте позиции с биржей не сверялись.

Теперь `trades` — единственный журнал во всех режимах. В TESTNET/LIVE:
- открытие — строка по факту исполненного ордера (record_open);
- закрытие — трекер увидел, что позиции на бирже нет, и строка закрывается по
  фактическому PnL биржи из closed-pnl (record_close);
- до ордера — одна позиция на символ по трекеру, бирже и журналу (open_position_reason);
- при старте — сверка журнала с биржей (reconcile_on_startup).
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import List, Optional, Tuple

import database
from execution.position_tracker import TrackedPosition

logger = logging.getLogger(__name__)

CLOSED_PNL_ATTEMPTS = 3
CLOSED_PNL_DELAY_SECONDS = 2.0
# Bybit отбирает closed-pnl по createdTime ЗАКРЫВАЮЩЕГО ордера. Тейк и стоп создаются
# вместе с позицией — на доли секунды раньше отметки открытия у бота, и поиск «с
# момента открытия» их не видел: каждое закрытие стопом или тейком записывалось
# оценкой (демо-счёт 11.09.2026, UNIUSDT: +0,038 $ вместо +0,082 $).
CLOSED_PNL_LOOKBACK_MS = 10 * 60 * 1000
CLOSED_PNL_MAX_WINDOW_MS = 7 * 24 * 3600 * 1000 - 60 * 1000  # окно запроса биржи — до 7 дней
# Закрытия, записанные без PnL биржи; correct_estimated_closes уточняет их позже
ESTIMATED_CLOSE_REASONS = ("EXCHANGE_CLOSE_PNL_ESTIMATED", "CLOSED_WHILE_OFFLINE_PNL_UNKNOWN")


def _client():
    from exchange.bybit_client import get_bybit_client
    return get_bybit_client()


def _tracker():
    from execution.position_tracker import get_position_tracker
    return get_position_tracker()


def _parse_time(when) -> datetime:
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when


def _side(exchange_side: str) -> str:
    return "LONG" if exchange_side == "Buy" else "SHORT"


def _ms(when) -> int:
    return int(_parse_time(when).timestamp() * 1000)


def closed_pnl_since(client, symbol: str, opened_at, until=None) -> Tuple[Optional[float], Optional[float]]:
    """
    (PnL, цена выхода) закрытий по символу после открытия сделки — или (None, None).

    Запрос — с запасом до открытия (CLOSED_PNL_LOOKBACK_MS): биржа отбирает записи по
    времени создания закрывающего ордера, а тейк и стоп создаются вместе с позицией.
    Отбор — по времени самого закрытия (updatedTime): не раньше открытия (закрытие
    прошлой сделки по символу — не наше) и не позже until (открытие следующей).
    """
    opened_ms = _ms(opened_at)
    start_ms = max(opened_ms - CLOSED_PNL_LOOKBACK_MS, int(time.time() * 1000) - CLOSED_PNL_MAX_WINDOW_MS)
    until_ms = _ms(until) if until else None
    records = [
        r for r in client.get_closed_pnl(symbol, start_ms)
        if int(r.get("updatedTime") or 0) >= opened_ms
        and (until_ms is None or int(r.get("updatedTime") or 0) <= until_ms)
    ]
    if not records:
        return None, None
    pnl = sum(float(r.get("closedPnl") or 0) for r in records)
    latest = max(records, key=lambda r: int(r.get("updatedTime") or 0))
    return pnl, (float(latest.get("avgExitPrice") or 0) or None)


def record_open(symbol: str, side: str, entry: float, stop: Optional[float], target: Optional[float],
                qty: float, leverage, order_id: str, strategy_name: Optional[str] = None) -> int:
    """Строка журнала по исполненному ордеру. Номинал — количество × цена входа."""
    return database.add_trade(
        symbol, side, float(entry), float(stop or 0.0), float(target or 0.0),
        position_size=float(qty) * float(entry), leverage=leverage,
        strategy_name=strategy_name, exchange_order_id=order_id,
    )


def _open_trade_for(symbol: str) -> Optional[dict]:
    return next((t for t in database.get_open_trades() if t["symbol"] == symbol), None)


def record_close(tracked: TrackedPosition, client=None, sleep=time.sleep) -> float:
    """
    Закрыть строку журнала по позиции, которой больше нет на бирже. PnL — из
    closed-pnl (он появляется с задержкой, поэтому несколько попыток); не нашёлся —
    последний нереализованный PnL, и причина закрытия это честно говорит.
    """
    client = client or _client()
    pnl = price = None
    for attempt in range(1, CLOSED_PNL_ATTEMPTS + 1):
        try:
            pnl, price = closed_pnl_since(client, tracked.symbol, tracked.opened_at)
        except Exception as e:
            logger.warning("record_close %s: closed-pnl недоступен: %s", tracked.symbol, e)
        if pnl is not None:
            break
        if attempt < CLOSED_PNL_ATTEMPTS:
            sleep(CLOSED_PNL_DELAY_SECONDS)

    reason = "EXCHANGE_CLOSE"
    if pnl is None:
        pnl = tracked.unrealised_pnl
        reason = "EXCHANGE_CLOSE_PNL_ESTIMATED"
        logger.warning("record_close %s: closed-pnl не нашёлся — PnL по последнему опросу %.2f", tracked.symbol, pnl)
    close_price = price or tracked.current_price or tracked.entry_price

    trade = _open_trade_for(tracked.symbol)
    if trade:
        database.close_trade(trade["id"], close_price, reason, pnl)
    else:
        logger.warning("record_close: в журнале нет открытой сделки %s", tracked.symbol)
    if tracked.order_id:
        database.close_position_by_order_id(tracked.order_id, close_price, reason, pnl)
    return pnl


def correct_estimated_closes(client=None, days: int = 7) -> int:
    """
    Уточнить по бирже закрытия, записанные оценкой (отчёт биржи о закрытии появляется
    с задержкой). Закрытие ищется между открытием сделки и открытием следующей по тому
    же символу. Возвращает число уточнённых сделок. Вызывается каждый оборот анализа.
    """
    estimated = database.get_estimated_closes(ESTIMATED_CLOSE_REASONS, days)
    if not estimated:
        return 0
    client = client or _client()
    fixed = 0
    for trade in estimated:
        try:
            pnl, price = closed_pnl_since(client, trade["symbol"], trade["timestamp"], until=trade.get("next_open"))
        except Exception as e:
            logger.warning("Уточнение PnL #%s %s: closed-pnl недоступен: %s", trade["id"], trade["symbol"], e)
            continue
        if pnl is None:
            continue
        if database.correct_trade_close(trade["id"], price or trade.get("close_price") or 0.0, pnl,
                                        "EXCHANGE_CLOSE", ESTIMATED_CLOSE_REASONS):
            fixed += 1
            logger.info("PnL сделки #%s %s уточнён по бирже: оценка %.4f → %.4f",
                        trade["id"], trade["symbol"], float(trade.get("pnl") or 0.0), pnl)
    return fixed


def open_position_reason(symbol: str, client, tracker) -> Optional[str]:
    """
    Почему по символу нельзя открывать позицию — или None. Проверка до ордера и
    с отказом при любой неясности: не удалось спросить биржу — не открываем.
    """
    if any(p.symbol == symbol for p in tracker.get_all_active()):
        return "позиция по символу уже отслеживается"
    try:
        live = [p for p in client.get_positions(symbol) if p.size > 0]
    except Exception as e:
        return f"не удалось проверить позиции на бирже: {e}"
    if live:
        return "на бирже уже есть позиция по символу"
    if _open_trade_for(symbol):
        return "в журнале уже есть открытая сделка по символу"
    return None


@dataclass
class ReconcileReport:
    tracked: List[str] = field(default_factory=list)
    closed: List[str] = field(default_factory=list)
    adopted: List[str] = field(default_factory=list)
    messages: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"в трекере {len(self.tracked)}, закрыто за время простоя {len(self.closed)}, "
                f"взято под учёт {len(self.adopted)}")


def reconcile_on_startup(client=None, tracker=None) -> ReconcileReport:
    """
    Сверить журнал с биржей при старте. Сбой запроса позиций — исключение:
    вызывающий ставит торговлю на паузу, потому что без сверки неизвестно,
    что уже открыто.
    """
    client = client or _client()
    tracker = tracker or _tracker()
    exchange = {p.symbol: p for p in client.get_positions() if p.size > 0}
    report = ReconcileReport()

    for trade in database.get_open_trades():
        symbol = trade["symbol"]
        live = exchange.pop(symbol, None)
        if live is None:
            pnl, price = closed_pnl_since(client, symbol, trade["timestamp"])
            reason = "CLOSED_WHILE_OFFLINE" if pnl is not None else "CLOSED_WHILE_OFFLINE_PNL_UNKNOWN"
            close_price = price or trade["entry"]
            database.close_trade(trade["id"], close_price, reason, pnl if pnl is not None else 0.0)
            if trade.get("exchange_order_id"):
                database.close_position_by_order_id(trade["exchange_order_id"], close_price, reason,
                                                    pnl if pnl is not None else 0.0)
            report.closed.append(symbol)
            report.messages.append(
                f"• {symbol}: закрыта на бирже, пока бот не работал — PnL {pnl:+.2f} USDT" if pnl is not None
                else f"• {symbol}: закрыта на бирже, пока бот не работал — PnL не найден, записан 0"
            )
            continue

        side = _side(live.side)
        if side != trade["side"]:
            report.messages.append(f"• {symbol}: в журнале {trade['side']}, на бирже {side} — в трекер взята позиция биржи")
        tracker.add(TrackedPosition(
            symbol=symbol, side=side, entry_price=live.entry_price, qty=live.size,
            stop_loss=live.stop_loss, take_profit=live.take_profit,
            order_id=trade.get("exchange_order_id") or "", opened_at=_parse_time(trade["timestamp"]),
        ))
        report.tracked.append(symbol)

    for symbol, live in exchange.items():
        side = _side(live.side)
        database.add_trade(
            symbol, side, live.entry_price, live.stop_loss or 0.0, live.take_profit or 0.0,
            position_size=live.size * live.entry_price, leverage=live.leverage,
            strategy_name="adopted_from_exchange",
        )
        tracker.add(TrackedPosition(
            symbol=symbol, side=side, entry_price=live.entry_price, qty=live.size,
            stop_loss=live.stop_loss, take_profit=live.take_profit, order_id="",
        ))
        report.adopted.append(symbol)
        report.messages.append(
            f"• {symbol} {side}: позиция на бирже без записи в журнале — взята под учёт"
            + ("" if live.stop_loss else ", СТОПА НА БИРЖЕ НЕТ")
        )
    return report
