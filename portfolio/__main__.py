"""
Цикл исполнителя И14: `python -m portfolio`. Раз в час:
  • условие старта — журнал бота без открытых сделок и ни одной позиции на счёте (две последние
    сделки бота закрываются сами); пока не выполнено — ждём и пишем событие;
  • понедельник, 00:02–23:59 UTC, ребалансировка ещё не сделана — считаем веса по замороженным
    сигналам И4/И3/И17а и доводим позиции до целей; не прошедшие ордера повторяются следующим часом;
  • снимок стоимости и позиций, начисления фандинга за 48 ч, пульс;
  • просадка от пика > 25 % капитала — всё закрыть, остановиться, сообщить владельцу.
"""
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import List

import config
from backtest import trend_ts as tt
from portfolio import calendar, engine
from portfolio.client import PortfolioClient
from portfolio.store import Store

CYCLE_SEC = 3600
SYNC_BACK_MS = 48 * 3600 * 1000
logger = logging.getLogger("portfolio")


def capital() -> float:
    return float(os.environ.get("PORTFOLIO_CAPITAL_USDT", "1000"))


def start_this_week() -> bool:
    from utils.env import env_flag
    return env_flag("PORTFOLIO_START_THIS_WEEK", False)


def root_dir() -> Path:
    return Path(os.environ.get("PORTFOLIO_DIR", "/portfolio"))


def symbols() -> List[str]:
    return sorted(set(config.SYMBOLS) | set(tt.SYMBOLS))


BLOCKED_CODE = "110126"      # Bybit: контракт требует подписи соглашения (токенизированные акции) — в корзину не ставим


def blocked_symbols(store: Store) -> set:
    return set(store.keys("blocked:"))


def remember_blocked(store: Store, symbol: str, error: str, now: int) -> bool:
    if BLOCKED_CODE not in error:
        return False
    store.set(f"blocked:{symbol}", now)
    store.event(now, "blocked", f"{symbol}: {error[:120]}")
    return True


def notify(text: str) -> None:
    """Сообщение владельцу; у исполнителя нет цикла событий бота — свой (14.09: адаптер бота падал без него)."""
    try:
        import asyncio
        from telegram_bot import send_message_async
        asyncio.run(send_message_async(text, parse_mode=None))
    except Exception:
        logger.warning("И14: сообщение владельцу не отправлено", exc_info=True)


def ready_to_start(cli, store: Store, now: int) -> bool:
    """Старт — когда журнал бота пуст и на счёте нет позиций; условие проверяется один раз."""
    if store.get("started_at"):
        return True
    import database
    open_trades = database.get_open_trades()
    positions = cli.positions_qty()
    if open_trades or positions:
        store.event(now, "waiting", f"сделок бота в журнале {len(open_trades)}, позиций на счёте {len(positions)}")
        return False
    store.set("started_at", now)
    store.set("start_equity", cli.usdt_equity())
    # Первая ребалансировка — ближайший понедельник ПОСЛЕ запуска (правило И14): текущая неделя
    # считается сделанной. 14.09 без этого исполнитель ребалансировался в день запуска.
    store.set("last_rebalance_t", engine.monday_of(now))
    store.event(now, "start", f"первая ребалансировка {engine.next_rebalance(now)}")
    notify("📊 И14 запущен: позиций нет, первая ребалансировка — ближайший понедельник 00:02 UTC")
    return True


def rebalance(cli, store: Store, t: int, now: int) -> None:
    ages = cli.launch_ages_d()
    blocked = blocked_symbols(store)
    candidates = [s for s in cli.continuation_candidates(ages) if s not in blocked]  # третья нога И17а
    syms = sorted(set(symbols()) | set(candidates))
    data = cli.market_data(syms, t, ages)
    weights = engine.combined_weights(data, list(tt.SYMBOLS), list(config.SYMBOLS), t, candidates)
    cap = capital()
    targets = {s: w * cap for s, w in weights.items()}
    positions = cli.positions_usdt()
    marks = {s: cli.get_mark_price(s) for s in set(targets) | set(positions)}
    filters = {s: cli.get_instrument_filters(s) for s in marks}
    orders = engine.orders_to_target(targets, positions, marks, filters, engine.min_order_usdt(cap))
    done, failed = [], []
    for o in orders:
        try:
            if not o.reduce_only:
                cli.set_leverage(o.symbol, engine.LEVERAGE)
            cli.place_order(o.symbol, o.side, o.qty, reduce_only=o.reduce_only)
            done.append([o.symbol, o.side, str(o.qty)])
        except Exception as exc:   # один ордер не прошёл — остальные идут, повтор следующим часом
            err = f"{type(exc).__name__}: {exc}"[:160]
            if remember_blocked(store, o.symbol, err, now):
                weights.pop(o.symbol, None)
                continue
            failed.append([o.symbol, o.side, str(o.qty), err])
    store.rebalance(t, now, weights, done, failed)
    if not failed:
        store.set("last_rebalance_t", t)
    store.event(now, "rebalance", f"монет {len(weights)}, ордеров {len(done)}, не прошло {len(failed)}")
    notify(f"📊 И14 ребалансировка: монет {len(weights)}, ордеров {len(done)}"
           + (f", НЕ ПРОШЛО {len(failed)} — повтор через час" if failed else ""))


def close_all(cli, store: Store, now: int, reason: str) -> None:
    for s, q in cli.positions_qty().items():
        cli.place_order(s, "Sell" if q > 0 else "Buy", abs(q), reduce_only=True)
    store.set("halted", reason)
    store.event(now, "halt", reason)
    notify(f"🛑 И14 остановлен по правилу: {reason}. Все позиции закрыты.")


def cycle(cli, store: Store, now: int) -> None:
    if not store.get("halted") and ready_to_start(cli, store, now):
        equity = cli.usdt_equity()
        peak = max(float(store.get("peak_equity") or 0), equity)
        store.set("peak_equity", peak)
        if engine.drawdown_halt(peak, equity, capital()):
            close_all(cli, store, now, f"просадка {peak - equity:.0f} USDT > {engine.MAX_DRAWDOWN * capital():.0f}")
        else:
            last = store.get("last_rebalance_t")
            t = engine.due_rebalance(now, int(last) if last else None)
            if t is None and start_this_week() and not store.has_rebalance(engine.monday_of(now)):
                # Поправка правила 14.09 (владелец): догоняющая ребалансировка в неделю запуска по
                # сигналам её понедельника — один раз: запись в rebalances исключает повтор.
                t = engine.monday_of(now)
                store.event(now, "catch_up", f"сигналы понедельника {t}")
            if t is not None:
                rebalance(cli, store, t, now)
    store.add_funding(cli.settlements(now - SYNC_BACK_MS))
    store.snapshot(now, cli.usdt_equity(), cli.positions_usdt())
    for key, text in calendar.due(now, lambda k: store.get(f"notice:{k}") is not None):  # контрольные даты программы
        notify(text)
        store.set(f"notice:{key}", now)
    (root_dir() / "heartbeat").write_text(f"{now / 1000:.0f}\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cli = PortfolioClient()
    root_dir().mkdir(parents=True, exist_ok=True)
    store = Store(str(root_dir() / "portfolio.db"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            cycle(cli, store, int(time.time() * 1000))
            logger.info("цикл И14 выполнен")
        except Exception as exc:
            logger.warning("цикл И14 не удался: %s", type(exc).__name__, exc_info=True)
            store.event(int(time.time() * 1000), "error", f"{type(exc).__name__}: {exc}"[:300])
        stop.wait(CYCLE_SEC)


if __name__ == "__main__":
    main()
