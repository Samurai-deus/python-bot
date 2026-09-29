"""
Цикл исполнителя И18: `python -m btcalts`. Раз в час на отдельном демо-субсчёте:
  • старт — один раз: если доступных USDT меньше капитала, запрашиваются демо-средства; стартовая
    стоимость — USDT-часть счёта; текущая неделя считается сделанной — первая ребалансировка в
    ближайший понедельник 00:02 UTC (по правилу И18: с 21.09.2026, вместе с бумагой);
  • понедельник, 00:02–23:59 UTC, ребалансировка ещё не сделана — лонг BTC 50 % / шорт 30 самых
    ликвидных альтов 50 %, ордера до цели; не прошедшие монеты повторяются следующим часом по тем же
    целям; владельцу —
    сообщение по каждой ребалансировке;
  • снимок стоимости и позиций, начисления фандинга за 48 ч, пульс;
  • просадка от пика > 25 % капитала (подтверждённая вторым чтением) — всё закрыть, остановиться,
    сообщить; не закрытое — дозакрывается каждый час. Цикл — в hh:02 UTC (portfolio.executor).
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
from portfolio import executor
from portfolio.leftovers import close_leftovers
from portfolio.store import Store

SYNC_BACK_MS = 48 * 3600 * 1000
DEMO_APPLY_MAX = 100_000
logger = logging.getLogger("btcalts")


def capital() -> float:
    from utils.env import env_float
    return env_float("BTCALTS_CAPITAL_USDT", 5000.0)


def root_dir() -> Path:
    return Path(os.environ.get("BTCALTS_DIR", "/btcalts"))


def start_this_week() -> bool:
    """Поправка 15.09 (владелец: «запускай уже сейчас»): догоняющая ребалансировка в неделю запуска, один раз."""
    from utils.env import env_flag
    return env_flag("BTCALTS_START_THIS_WEEK", False)


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
    equity = cli.usdt_equity(strict=False)          # пустой субсчёт до зачисления демо-средств
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


def target_weights(cli, store: Store, t: int) -> dict:
    ages = cli.launch_ages_d()
    blocked = executor.blocked_symbols(store)
    candidates = [s for s in cli.continuation_candidates(ages) if s not in blocked]
    data = cli.market_data(sorted(set(candidates) | {engine.BTC}), t, ages)
    return engine.weights(data, candidates)


def rebalance(cli, store: Store, t: int, now: int) -> None:
    retry = store.week_retry(t)
    weights, only = (retry[0], retry[1]) if retry else (target_weights(cli, store, t), None)
    done, failed = executor.trade_to_weights(cli, store, weights, capital(), engine.LEVERAGE, now, only)
    store.rebalance(t, now, weights, done, failed)
    if not failed:
        store.set("last_rebalance_t", t)
    shorts = [s for s, w in weights.items() if w < 0]
    store.event(now, "rebalance", f"альтов {len(shorts)}, ордеров {len(done)}, не прошло {len(failed)}")
    notify(f"📉 И18 ребалансировка: лонг BTC, шорт {len(shorts)} альтов ({', '.join(s[:-4] for s in shorts[:8])}…), "
           f"ордеров {len(done)}" + (f", НЕ ПРОШЛО {len(failed)} — повтор через час" if failed else ""))


def cycle(cli, store: Store, now: int) -> None:
    store.set("capital", capital())
    if store.get("halted"):
        executor.after_halt(cli, store, now, notify, "И18")
    elif ready_to_start(cli, store, now):
        reason = executor.drawdown_confirmed(cli, store, capital(), now, engine.MAX_DRAWDOWN)     # стоп правила И18
        if reason:
            executor.halt(cli, store, now, reason, notify, "И18")
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
    from utils.log_redaction import setup_service_logging
    setup_service_logging()
    cli = BtcAltsClient(*keys_from_env())
    root_dir().mkdir(parents=True, exist_ok=True)
    store = Store(str(root_dir() / "btcalts.db"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        error = None
        try:
            cycle(cli, store, int(time.time() * 1000))
            logger.info("цикл И18 выполнен")
        except Exception as exc:
            logger.warning("цикл И18 не удался: %s", type(exc).__name__, exc_info=True)
            error = f"{type(exc).__name__}: {exc}"
            store.event(int(time.time() * 1000), "error", error[:300])
        try:
            executor.cycle_outcome(store, int(time.time() * 1000), error, notify, "И18")
        except Exception:
            logger.warning("учёт сбоев цикла И18 не удался", exc_info=True)
        stop.wait(executor.sleep_seconds(int(time.time() * 1000)))


if __name__ == "__main__":
    main()
