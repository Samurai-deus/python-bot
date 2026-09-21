"""
Цикл исполнителя И13: `python -m carry`. Раз в час:
  1. один раз — чистый старт: стартовые демо-монеты субсчёта (BTC, ETH) продаются в USDT, чтобы
     стоимость счёта отражала только сбор фандинга; при нехватке USDT — запрос демо-средств;
  2. один раз — открытие пар: спот LONG, затем контракт SHORT на тот же объём (плечо 1×);
  3. каждый час — подгонка хеджа (> 2 %), экстренное закрытие при опасной марже, запись
     исполнений и фандинга за 48 ч, снимок стоимости и расхождений, пульс.
Сбой открытия шорта после покупки спота чинит подгонка хеджа следующего цикла; спот по монете
второй раз не покупается (метка bought:<символ>), недооткрытые монеты открываются следующим циклом.
"""
import logging
import os
import signal
import threading
import time
from pathlib import Path
from typing import Dict, List

from carry import engine
from carry.client import CarryClient, keys_from_env
from carry.store import Store
from portfolio import engine as pe

CYCLE_SEC = 3600
SYNC_BACK_MS = 48 * 3600 * 1000
MIN_SPOT_USDT = 5.0
# На пару нужно ≈ 2,2 номинала: спот оплачивается полностью (1×), шорт при плече 1× держит маржу
# в размер номинала (1×), 10 % — запас. 14.09 при 1,2 на пару средств хватило только на BTC —
# ETH-спот отклонён биржей (170131 Insufficient balance).
PAIR_USDT_FACTOR = 2.2
DEMO_APPLY_MAX = 100_000               # Bybit: не больше 100 000 USDT за запрос

logger = logging.getLogger("carry")


def symbols() -> List[str]:
    return [s for s in os.environ.get("CARRY_SYMBOLS", "BTCUSDT,ETHUSDT").split(",") if s]


def notional() -> float:
    return float(os.environ.get("CARRY_NOTIONAL_USDT", "10000"))


def carry_dir() -> Path:
    return Path(os.environ.get("CARRY_DIR", "/carry"))


def base(symbol: str) -> str:
    return symbol[:-4]


def notify(text: str) -> bool:
    """Сообщение владельцу; True — ушло. Отметки «отправлено» ставятся только по True."""
    from telegram_bot import send_owner_blocking
    ok = send_owner_blocking(text)
    if not ok:
        logger.warning("И13: сообщение владельцу не отправлено: %s", text[:80])
    return ok


def weekly_summary_due(now: int, store: Store) -> bool:
    """
    Понедельник с 01:00 UTC до конца суток и сводка за эту неделю ещё не отправлена. Окно — сутки, а не час:
    неудачная отправка повторяется следующим циклом (21.09.2026 сводка не ушла и пропала бы до следующей недели).
    """
    monday = pe.monday_of(now)
    hour = (now - monday) // 3_600_000
    return 1 <= hour < 24 and store.get(f"weekly:{monday}") is None


def send_weekly_summary(store: Store, equity: float, deviations: Dict[str, float], now: int) -> bool:
    """Сводка уходит — ставится отметка недели; не ушла — отметки нет, повтор следующим циклом."""
    if not weekly_summary_due(now, store):
        return False
    if notify(weekly_summary(store, equity, deviations, now)):
        store.set(f"weekly:{pe.monday_of(now)}", now)
        return True
    return False


def weekly_summary(store: Store, equity: float, deviations: Dict[str, float], now: int) -> str:
    start = store.get("start_equity")
    start_eq = float(start) if start else equity
    funding = store.conn.execute("SELECT COALESCE(SUM(change), 0) FROM funding").fetchone()[0]
    dev = ", ".join(f"{base(s)} {100 * d:+.1f} %" for s, d in deviations.items()) or "—"
    return (f"📊 И13 неделя: стоимость {equity:.2f} против старта {start_eq:.2f} ({equity - start_eq:+.2f} USDT); "
            f"фандинг всего {float(funding):+.2f} USDT; отклонение хеджа: {dev}"
            + ("; ОСТАНОВЛЕН: " + store.get("halted") if store.get("halted") else ""))


def ensure_usdt(cli, store: Store, need: float, now: int) -> None:
    """Доступного меньше need — запросить демо-средства (демо-деньги, стоимость счёта считается от старта пар)."""
    avail = cli.available_usd()
    if avail < need:
        amount = min(DEMO_APPLY_MAX, need - avail + 1000)
        cli.apply_demo_usdt(amount)
        store.event(now, "demo_funds", f"доступно {avail:.0f} < {need:.0f}, запрошено {amount:.0f}")


def clean_start(cli, store: Store, syms: List[str], now: int) -> None:
    if store.get("cleaned_at"):
        return
    bal = cli.coin_balances()
    for s in syms:
        price = cli.spot_price(s)
        qty = engine.floor_step(bal.get(base(s), 0.0), cli.spot_base_step(s))
        if qty * price >= MIN_SPOT_USDT:
            cli.spot_market(s, "Sell", qty)
            store.event(now, "clean_sell", f"{s} {qty}")
    ensure_usdt(cli, store, notional() * len(syms) * PAIR_USDT_FACTOR, now)
    store.set("cleaned_at", now)


def open_pairs(cli, store: Store, syms: List[str], now: int) -> None:
    """Открыть пары по монетам, у которых спот ещё не куплен (метка bought:<символ> — сразу после покупки)."""
    if store.get("opened_at"):
        return
    for s in syms:
        if store.get(f"bought:{s}"):
            continue
        ensure_usdt(cli, store, notional() * PAIR_USDT_FACTOR, now)   # и восстановление после нехватки
        step = cli.get_qty_step(s)
        qty = engine.pair_qty(notional(), cli.spot_price(s), step, step)
        if not qty:
            store.event(now, "skip", f"{s}: объём меньше шага контракта")
            continue
        cli.set_leverage(s, 1)
        cli.spot_market(s, "Buy", qty)
        store.set(f"bought:{s}", now)      # шорт не открылся — догонит подгонка хеджа, спот второй раз не купится
        cli.perp_market(s, "Sell", qty)
        store.event(now, "open", f"{s} {qty}")
    store.set("opened_at", now)


def rehedge(cli, store: Store, syms: List[str], now: int) -> Dict[str, float]:
    """Подогнать шорт к споту; вернуть расхождения после подгонки (до повторного опроса — по расчёту)."""
    bal = cli.coin_balances()
    deviations = {}
    for s in syms:
        spot_qty, short = bal.get(base(s), 0.0), cli.short_qty(s)
        adj = engine.hedge_adjustment(spot_qty, short, cli.get_qty_step(s))
        if adj:
            if adj > 0:
                cli.perp_market(s, "Sell", adj)
            else:
                cli.perp_market(s, "Buy", -adj, reduce_only=True)
            store.event(now, "rehedge", f"{s} spot {spot_qty} short {short} adj {adj:+}")
            short += adj
        deviations[s] = engine.hedge_deviation(spot_qty, short)
    return deviations


def emergency_close(cli, store: Store, syms: List[str], now: int, reason: str) -> None:
    for s in syms:
        short = cli.short_qty(s)
        if short > 0:
            cli.perp_market(s, "Buy", short, reduce_only=True)
        qty = engine.floor_step(cli.coin_balances().get(base(s), 0.0), cli.spot_base_step(s))
        if qty * cli.spot_price(s) >= MIN_SPOT_USDT:
            cli.spot_market(s, "Sell", qty)
    store.set("halted", reason)
    store.event(now, "emergency_close", reason)


def sync(cli, store: Store, now: int) -> None:
    since = now - SYNC_BACK_MS
    for category in ("linear", "spot"):
        store.add_fills(category, cli.executions(category, since))
    store.add_funding(cli.settlements(since))


def cycle(cli, store: Store, now: int) -> None:
    syms = symbols()
    deviations: Dict[str, float] = {}
    if not store.get("halted"):
        clean_start(cli, store, syms, now)
        open_pairs(cli, store, syms, now)
        mm = cli.account_mm_rate()
        if engine.margin_danger(mm):
            emergency_close(cli, store, syms, now, f"accountMMRate {mm}")
        else:
            deviations = rehedge(cli, store, syms, now)
    sync(cli, store, now)
    equity = cli.total_equity()
    if store.get("opened_at") and store.get("start_equity") is None:
        store.set("start_equity", equity)
    bal = cli.coin_balances()
    positions = {s: {"spot": bal.get(base(s), 0.0), "short": cli.short_qty(s), "price": cli.spot_price(s)} for s in syms}
    store.snapshot(now, equity, cli.account_mm_rate(), deviations, positions)
    send_weekly_summary(store, equity, deviations, now)
    (carry_dir() / "heartbeat").write_text(f"{now / 1000:.0f}\n", encoding="utf-8")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cli = CarryClient(*keys_from_env())
    carry_dir().mkdir(parents=True, exist_ok=True)
    store = Store(str(carry_dir() / "carry.db"))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    while not stop.is_set():
        try:
            cycle(cli, store, int(time.time() * 1000))
            logger.info("цикл И13 выполнен")
        except Exception as exc:  # биржа, сеть — следующий цикл; пульс покажет долгий сбой
            logger.warning("цикл И13 не удался: %s", type(exc).__name__, exc_info=True)
            store.event(int(time.time() * 1000), "error", f"{type(exc).__name__}: {exc}"[:300])
        stop.wait(CYCLE_SEC)


if __name__ == "__main__":
    main()
