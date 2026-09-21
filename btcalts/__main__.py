"""
Цикл исполнителя И18: `python -m btcalts`. Раз в час на отдельном демо-субсчёте:
  • старт — один раз: если доступных USDT меньше капитала, запрашиваются демо-средства; стартовая
    стоимость — USDT-часть счёта; текущая неделя считается сделанной — первая ребалансировка в
    ближайший понедельник 00:02 UTC (по правилу И18: с 21.09.2026, вместе с бумагой);
  • понедельник, 00:02–23:59 UTC, ребалансировка ещё не сделана — лонг BTC 50 % / шорт 30 самых
    ликвидных альтов 50 %, ордера до цели; не прошедшие повторяются следующим часом; владельцу —
    сообщение по каждой ребалансировке;
  • снимок стоимости и позиций, начисления фандинга за 48 ч, пульс;
  • просадка от пика > 25 % капитала — всё закрыть, остановиться, сообщить.
"""
import logging
import os
import signal
import threading
import time
from pathlib import Path

from btcalts import engine
from btcalts.client import BtcAltsClient, keys_from_env
from portfolio import engine as pe
from portfolio.leftovers import close_leftovers
from portfolio.store import Store

CYCLE_SEC = 3600
SYNC_BACK_MS = 48 * 3600 * 1000
DEMO_APPLY_MAX = 100_000
logger = logging.getLogger("btcalts")


def capital() -> float:
    return float(os.environ.get("BTCALTS_CAPITAL_USDT", "5000"))


def root_dir() -> Path:
    return Path(os.environ.get("BTCALTS_DIR", "/btcalts"))


def start_this_week() -> bool:
    """Поправка 15.09 (владелец: «запускай уже сейчас»): догоняющая ребалансировка в неделю запуска, один раз."""
    from utils.env import env_flag
    return env_flag("BTCALTS_START_THIS_WEEK", False)


BLOCKED_CODE = "110126"      # Bybit: контракт требует подписи соглашения (токенизированные акции) — в корзину не ставим


def blocked_symbols(store: Store) -> set:
    return set(store.keys("blocked:"))


def remember_blocked(store: Store, symbol: str, error: str, now: int) -> bool:
    if BLOCKED_CODE not in error:
        return False
    store.set(f"blocked:{symbol}", now)
    store.event(now, "blocked", f"{symbol}: {error[:120]}")
    return True


def notify(text: str) -> bool:
    """Сообщение владельцу; True — ушло. Отметки «отправлено» ставятся только по True."""
    from telegram_bot import send_owner_blocking
    ok = send_owner_blocking(text)
    if not ok:
        logger.warning("И18: сообщение владельцу не отправлено: %s", text[:80])
    return ok


def ensure_funds(cli, store: Store, now: int) -> None:
    """Субсчёт создаётся пустым: доступных USDT меньше капитала — запросить демо-средства (2 капитала, но ≤ 100 000)."""
    avail = cli.available_usd()
    if avail < capital():
        amount = min(DEMO_APPLY_MAX, 2 * capital())
        cli.apply_demo_usdt(amount)
        store.event(now, "demo_funds", f"доступно {avail:.0f} < {capital():.0f}, запрошено {amount:.0f}")


def ready_to_start(cli, store: Store, now: int) -> bool:
    if store.get("started_at"):
        return True
    ensure_funds(cli, store, now)
    equity = cli.usdt_equity()
    if equity < capital():
        store.event(now, "waiting", f"USDT на субсчёте {equity:.0f} < капитала {capital():.0f}")
        return False
    store.set("started_at", now)
    store.set("start_equity", equity)
    store.set("last_rebalance_t", pe.monday_of(now))     # первая ребалансировка — ближайший понедельник после запуска
    store.event(now, "start", f"первая ребалансировка {pe.next_rebalance(now)}")
    notify(f"📉 И18 (лонг BTC / шорт альтов) запущен на демо-субсчёте: USDT {equity:.0f}, капитал {capital():.0f}; "
           f"первая ребалансировка — ближайший понедельник 00:02 UTC")
    return True


def rebalance(cli, store: Store, t: int, now: int) -> None:
    ages = cli.launch_ages_d()
    blocked = blocked_symbols(store)
    candidates = [s for s in cli.continuation_candidates(ages) if s not in blocked]
    data = cli.market_data(sorted(set(candidates) | {engine.BTC}), t, ages)
    weights = engine.weights(data, candidates)
    cap = capital()
    targets = {s: w * cap for s, w in weights.items()}
    positions = cli.positions_qty()
    marks = {s: cli.get_mark_price(s) for s in set(targets) | set(positions)}
    filters = {s: cli.get_instrument_filters(s) for s in marks}
    orders = pe.orders_to_target(targets, positions, marks, filters, pe.min_order_usdt(cap))
    done, failed = [], []
    for o in orders:
        try:
            if not o.reduce_only:
                cli.set_leverage(o.symbol, engine.LEVERAGE)
            cli.place_order(o.symbol, o.side, o.qty, reduce_only=o.reduce_only)
            done.append([o.symbol, o.side, str(o.qty)])
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"[:160]
            if remember_blocked(store, o.symbol, err, now):
                weights.pop(o.symbol, None)      # контракт вне корзины — цель по нему снята, повтора не будет
                continue
            failed.append([o.symbol, o.side, str(o.qty), err])
    store.rebalance(t, now, weights, done, failed)
    if not failed:
        store.set("last_rebalance_t", t)
    shorts = [s for s, w in weights.items() if w < 0]
    store.event(now, "rebalance", f"альтов {len(shorts)}, ордеров {len(done)}, не прошло {len(failed)}")
    notify(f"📉 И18 ребалансировка: лонг BTC, шорт {len(shorts)} альтов ({', '.join(s[:-4] for s in shorts[:8])}…), "
           f"ордеров {len(done)}" + (f", НЕ ПРОШЛО {len(failed)} — повтор через час" if failed else ""))


def close_all(cli, store: Store, now: int, reason: str) -> None:
    for s, q in cli.positions_qty().items():
        cli.place_order(s, "Sell" if q > 0 else "Buy", abs(q), reduce_only=True)
    store.set("halted", reason)
    store.event(now, "halt", reason)
    notify(f"🛑 И18 остановлен по правилу: {reason}. Все позиции закрыты.")


def cycle(cli, store: Store, now: int) -> None:
    if not store.get("halted") and ready_to_start(cli, store, now):
        equity = cli.usdt_equity()
        peak = max(float(store.get("peak_equity") or 0), equity)
        store.set("peak_equity", peak)
        if engine.drawdown_halt(peak, equity, capital()):
            close_all(cli, store, now, f"просадка {peak - equity:.0f} USDT > {engine.MAX_DRAWDOWN * capital():.0f}")
        else:
            last = store.get("last_rebalance_t")
            t = pe.due_rebalance(now, int(last) if last else None)
            if t is None and start_this_week() and not store.has_rebalance(pe.monday_of(now)):
                t = pe.monday_of(now)     # сигналы понедельника этой недели, вход по текущим ценам — один раз
                store.event(now, "catch_up", f"сигналы понедельника {t}")
            if t is not None:
                rebalance(cli, store, t, now)
            close_leftovers(cli, store, now, notify, "И18")
    store.add_funding(cli.settlements(now - SYNC_BACK_MS))
    store.snapshot(now, cli.usdt_equity(), cli.positions_usdt())
    (root_dir() / "heartbeat").write_text(f"{now / 1000:.0f}\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cli = BtcAltsClient(*keys_from_env())
    root_dir().mkdir(parents=True, exist_ok=True)
    store = Store(str(root_dir() / "btcalts.db"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            cycle(cli, store, int(time.time() * 1000))
            logger.info("цикл И18 выполнен")
        except Exception as exc:
            logger.warning("цикл И18 не удался: %s", type(exc).__name__, exc_info=True)
            store.event(int(time.time() * 1000), "error", f"{type(exc).__name__}: {exc}"[:300])
        stop.wait(CYCLE_SEC)


if __name__ == "__main__":
    main()
