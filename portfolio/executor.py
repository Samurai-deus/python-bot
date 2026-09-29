"""
Общие шаги исполнителей И14 и И18 (аудит 29.09.2026): ордера до цели с отказом вместо тихого
пропуска, повтор ТОЛЬКО не прошедших монет по целям той же недели, остановка по просадке,
подтверждённая вторым чтением, закрытие по одной позиции, ритм цикла по часам UTC.

До 29.09 эти шаги жили копиями в portfolio/__main__.py и btcalts/__main__.py, и каждая правка
делалась дважды.
"""
from typing import Callable, Dict, List, Mapping, Optional, Set, Tuple

from portfolio import engine
from portfolio.store import Store

BLOCKED_CODE = "110126"      # Bybit: контракт требует подписи соглашения (токенизированные акции) — в корзину не ставим
HOUR_MS = 3_600_000
# Цикл — в hh:02:05 UTC: ребалансировка по правилу в понедельник 00:02, а не в минуту, когда контейнер
# случайно стартовал (28.09 — 00:13). Пять секунд — запас на часы хоста.
CYCLE_OFFSET_MS = engine.REBALANCE_DELAY_MS + 5_000
NO_PRICE = "нет цены или параметров инструмента"
FAIL_ALERT_AFTER = 3        # циклов подряд со сбоем до сообщения владельцу


def next_cycle_ms(now_ms: int) -> int:
    """Ближайший момент hh:02:05 UTC строго после now_ms."""
    t = now_ms - now_ms % HOUR_MS + CYCLE_OFFSET_MS
    return t if t > now_ms else t + HOUR_MS


def sleep_seconds(now_ms: int) -> float:
    return max(1.0, (next_cycle_ms(now_ms) - now_ms) / 1000)


def blocked_symbols(store: Store) -> set:
    return set(store.keys("blocked:"))


def remember_blocked(store: Store, symbol: str, error: str, now: int) -> bool:
    if BLOCKED_CODE not in error:
        return False
    store.set(f"blocked:{symbol}", now)
    store.event(now, "blocked", f"{symbol}: {error[:120]}")
    return True


def own_positions(positions: Mapping[str, float], store: Store, targets: Mapping[str, float]) -> Dict[str, float]:
    """
    Позиции исполнителя: монеты из его журнала и из текущих целей. Чужие (бот на общем счёте И14)
    ребалансировка и остановка не трогают — как и закрытие хвостов.
    """
    own = store.traded_symbols() | set(targets)
    return {s: q for s, q in positions.items() if q and s in own}


def instrument_filters(cli, symbols) -> Dict[str, object]:
    """Параметры инструментов по одному: сбой по монете — None для неё, а не срыв всей ребалансировки."""
    out = {}
    for s in symbols:
        try:
            out[s] = cli.get_instrument_filters(s)
        except Exception:
            out[s] = None
    return out


def trade_to_weights(cli, store: Store, weights: Dict[str, float], cap: float, leverage: float, now: int,
                     only: Optional[Set[str]] = None) -> Tuple[List[list], List[list]]:
    """
    Ордера до целей weights × cap. only — повтор: торгуются только эти монеты (цели те же, что в первом
    прогоне недели), остальные позиции между ребалансировками не трогаются. Монета без цены или параметров
    инструмента — в failed (повтор через час), а не молча вне ребалансировки. Контракт, требующий
    соглашения, снимается с целей (weights меняется на месте).
    """
    targets = {s: w * cap for s, w in weights.items()}
    positions = own_positions(cli.positions_qty(), store, targets)
    symbols = set(targets) | set(positions)
    if only is not None:
        symbols &= only
    marks = {s: cli.get_mark_price(s) for s in symbols}
    filters = instrument_filters(cli, symbols)
    failed = [[s, "", "", NO_PRICE] for s in engine.untradeable({s: targets.get(s, 0.0) for s in symbols}, marks, filters)]
    orders = engine.orders_to_target({s: v for s, v in targets.items() if s in symbols},
                                     {s: q for s, q in positions.items() if s in symbols},
                                     marks, filters, engine.min_order_usdt(cap))
    done = []
    for o in orders:
        try:
            if not o.reduce_only:
                cli.set_leverage(o.symbol, leverage)
            cli.place_order(o.symbol, o.side, o.qty, reduce_only=o.reduce_only)
            done.append([o.symbol, o.side, str(o.qty)])
        except Exception as exc:   # один ордер не прошёл — остальные идут, повтор следующим часом
            err = f"{type(exc).__name__}: {exc}"[:160]
            if remember_blocked(store, o.symbol, err, now):
                weights.pop(o.symbol, None)
                continue
            failed.append([o.symbol, o.side, str(o.qty), err])
    return done, failed


def drawdown_confirmed(cli, store: Store, cap: float, now: int) -> Optional[str]:
    """
    Причина остановки или None. Пик обновляется по текущей стоимости; превышение порога проверяется
    вторым чтением: остановка необратима, и один сбойный ответ биржи не должен закончить эксперимент.
    """
    equity = cli.usdt_equity()
    peak = max(float(store.get("peak_equity") or 0), equity)
    store.set("peak_equity", peak)
    if not engine.drawdown_halt(peak, equity, cap):
        return None
    again = cli.usdt_equity()
    if not engine.drawdown_halt(peak, again, cap):
        store.event(now, "drawdown_unconfirmed", f"пик {peak:.2f}, чтение {equity:.2f}, повторное {again:.2f}")
        return None
    return f"просадка {peak - again:.0f} USDT > {engine.MAX_DRAWDOWN * cap:.0f}"


def close_positions(cli, positions: Mapping[str, float]) -> List[str]:
    """Закрыть позиции по одной; вернуть не закрытые (с причиной). Сбой по одной не останавливает остальные."""
    failed = []
    for s, q in sorted(positions.items()):
        try:
            o = engine.close_order(s, q)
            cli.place_order(o.symbol, o.side, o.qty, reduce_only=True)
        except Exception as exc:
            failed.append(f"{s}: {type(exc).__name__}: {exc}"[:120])
    return failed


def halt(cli, store: Store, now: int, reason: str, notify: Callable[[str], bool], name: str) -> None:
    """Остановка по правилу: отметка ставится сразу (решение принято), позиции закрываются по одной."""
    store.set("halted", reason)
    store.event(now, "halt", reason)
    failed = close_positions(cli, own_positions(cli.positions_qty(), store, {}))
    if failed:
        store.event(now, "halt_close_failed", "; ".join(failed)[:480])
    text = (f"🛑 {name} остановлен по правилу: {reason}. "
            + (f"НЕ ЗАКРЫТО {len(failed)} позиций — повтор каждый час." if failed else "Все позиции закрыты."))
    if notify(text):
        store.set("notice:halt", now)


def after_halt(cli, store: Store, now: int, notify: Callable[[str], bool], name: str) -> None:
    """После остановки: дозакрыть оставшиеся свои позиции и доставить сообщение, если оно не ушло."""
    left = own_positions(cli.positions_qty(), store, {})
    failed = close_positions(cli, left) if left else []
    if left:
        store.event(now, "halt_close", f"дозакрыто {len(left) - len(failed)} из {len(left)}"
                    + (f"; не закрыто: {'; '.join(failed)}" if failed else ""))
    if store.get("notice:halt") is None or (left and not failed):
        text = (f"🛑 {name} остановлен по правилу: {store.get('halted')}. "
                + (f"НЕ ЗАКРЫТО {len(failed)} позиций — повтор через час." if failed else "Все позиции закрыты."))
        if notify(text):
            store.set("notice:halt", now)


def cycle_outcome(store, now: int, error: Optional[str], notify: Callable[[str], bool], name: str) -> None:
    """
    Сбои цикла исполнителя (И13/И14/И18): после FAIL_ALERT_AFTER подряд — сообщение владельцу, после
    восстановления — ещё одно. До 29.09 сбой оставался только в журнале событий, а сторож замечал его по
    возрасту пульса через 2,5 ч. store — любое хранилище с get/set (у И13 своё).
    """
    streak = int(store.get("fail_streak") or 0)
    if error:
        streak += 1
        store.set("fail_streak", streak)
        if streak >= FAIL_ALERT_AFTER and not store.get("fail_alerted"):
            if notify(f"⚠️ {name}: {streak} цикла подряд со сбоем, последний: {error[:200]}"):
                store.set("fail_alerted", now)
        return
    if streak:
        store.set("fail_streak", 0)
    if store.get("fail_alerted"):
        if notify(f"✅ {name}: цикл снова проходит (перед этим сбоев подряд: {streak})"):
            store.set("fail_alerted", "")
