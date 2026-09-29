"""
Цикл исполнителя И14: `python -m portfolio`. Раз в час:
  • условие старта — журнал бота без открытых сделок и ни одной позиции на счёте (две последние
    сделки бота закрываются сами); пока не выполнено — ждём и пишем событие;
  • понедельник, 00:02–23:59 UTC, ребалансировка ещё не сделана — считаем веса по замороженным
    сигналам И4/И3/И17а и доводим позиции до целей; не прошедшие монеты повторяются следующим часом
    по тем же целям (остальные позиции не трогаются);
  • снимок стоимости и позиций, начисления фандинга за 48 ч, пульс;
  • просадка от пика > 25 % капитала (подтверждённая вторым чтением) — закрыть свои позиции,
    остановиться, сообщить владельцу; не закрытое — дозакрывается каждый час.
Цикл — в hh:02 UTC (portfolio.executor), общие шаги с И18 — там же.
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
from portfolio import calendar, engine, executor
from portfolio.leftovers import close_leftovers
from portfolio.client import demo_client
from portfolio.store import Store

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


def notify(text: str) -> bool:
    """Сообщение владельцу; True — ушло. Отметки «отправлено» ставятся только по True."""
    from telegram_bot import send_owner_blocking
    ok = send_owner_blocking(text)
    if not ok:
        logger.warning("И14: сообщение владельцу не отправлено: %s", text[:80])
    return ok


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


def target_weights(cli, store: Store, t: int) -> dict:
    ages = cli.launch_ages_d()
    blocked = executor.blocked_symbols(store)
    candidates = [s for s in cli.continuation_candidates(ages) if s not in blocked]  # третья нога И17а
    syms = sorted(set(symbols()) | set(candidates))
    data = cli.market_data(syms, t, ages)
    return engine.combined_weights(data, list(tt.SYMBOLS), list(config.SYMBOLS), t, candidates)


def rebalance(cli, store: Store, t: int, now: int) -> None:
    retry = store.week_retry(t)
    weights, only = (retry[0], retry[1]) if retry else (target_weights(cli, store, t), None)
    done, failed = executor.trade_to_weights(cli, store, weights, capital(), engine.LEVERAGE, now, only)
    store.rebalance(t, now, weights, done, failed)
    if not failed:
        store.set("last_rebalance_t", t)
    store.event(now, "rebalance", f"монет {len(weights)}, ордеров {len(done)}, не прошло {len(failed)}")
    notify(f"📊 И14 ребалансировка: монет {len(weights)}, ордеров {len(done)}"
           + (f", НЕ ПРОШЛО {len(failed)} — повтор через час" if failed else ""))


def cycle(cli, store: Store, now: int) -> None:
    if store.get("halted"):
        executor.after_halt(cli, store, now, notify, "И14")
    elif ready_to_start(cli, store, now):
        reason = executor.drawdown_confirmed(cli, store, capital(), now)
        if reason:
            executor.halt(cli, store, now, reason, notify, "И14")
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
            close_leftovers(cli, store, now, notify, "И14")
    store.add_funding(cli.settlements(now - SYNC_BACK_MS))
    store.snapshot(now, cli.usdt_equity(), cli.positions_usdt())
    for key, text in calendar.due(now, lambda k: store.get(f"notice:{k}") is not None):  # контрольные даты программы
        if notify(text):                 # не ушло — отметки нет, повтор следующим часом (окно — сутки)
            store.set(f"notice:{key}", now)
    (root_dir() / "heartbeat").write_text(f"{now / 1000:.0f}\n", encoding="utf-8")


def main() -> None:
    from utils.log_redaction import setup_service_logging
    setup_service_logging()
    cli = demo_client()
    root_dir().mkdir(parents=True, exist_ok=True)
    store = Store(str(root_dir() / "portfolio.db"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        error = None
        try:
            cycle(cli, store, int(time.time() * 1000))
            logger.info("цикл И14 выполнен")
        except Exception as exc:
            logger.warning("цикл И14 не удался: %s", type(exc).__name__, exc_info=True)
            error = f"{type(exc).__name__}: {exc}"
            store.event(int(time.time() * 1000), "error", error[:300])
        try:
            executor.cycle_outcome(store, int(time.time() * 1000), error, notify, "И14")
        except Exception:
            logger.warning("учёт сбоев цикла И14 не удался", exc_info=True)
        stop.wait(executor.sleep_seconds(int(time.time() * 1000)))


if __name__ == "__main__":
    main()
