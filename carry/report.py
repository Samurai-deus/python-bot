"""
Отчёт И13: `python -m carry.report [--model]`. Срок, стоимость счёта против старта, полученный
фандинг и комиссии (из своей базы), просадка по часовым снимкам, доля часов с хеджем вне ±5 %,
события. --model — расчётный фандинг по опубликованным ставкам Bybit за срок × номинал
(второй критерий правила). Промежуточные отчёты — только о работе исполнителя, не об итоге.
"""
import argparse
import json
import sqlite3
import time
from typing import Dict, List

from carry import engine
from carry.__main__ import carry_dir, notional, symbols

DAY = 86_400_000
FUNDING_PAGE = 200
OPENING_WINDOW_MS = 15 * 60_000


def summary(conn: sqlite3.Connection) -> Dict:
    state = dict(conn.execute("SELECT key, value FROM state").fetchall())
    snaps = conn.execute("SELECT ts, equity, deviations FROM snapshots ORDER BY ts").fetchall()
    opened = int(state["opened_at"]) if state.get("opened_at") else None
    start_eq = float(state["start_equity"]) if state.get("start_equity") else None
    peak, dd = None, 0.0
    outside = total = 0
    for ts, eq, devs in snaps:
        if opened is None or ts < opened:
            continue
        peak = eq if peak is None else max(peak, eq)
        dd = max(dd, peak - eq)
        for v in json.loads(devs or "{}").values():
            total += 1
            outside += v > engine.NEAR_HEDGE
    funding = conn.execute("SELECT COALESCE(SUM(change), 0) FROM funding WHERE ts >= ?", (opened or 0,)).fetchone()[0]
    fee_usdt = "fee * CASE WHEN fee_coin IN ('USDT', '') THEN 1 ELSE price END"
    fees_usdt = conn.execute(f"SELECT COALESCE(SUM({fee_usdt}), 0) FROM fills WHERE ts >= ?", (opened or 0,)).fetchone()[0]
    # До 29.09 старт писался в конце цикла открытия, уже после покупок (запуск 14.09): комиссии открытия в
    # итог не попадали, а правило считает «итог после всех издержек» — вычитаются явно (исполнения цикла
    # открытия: до opened_at + 15 мин). С 29.09 старт пишется до покупок (start_before_open) — вычитать нечего.
    opening_fees = 0.0
    if opened and not state.get("start_before_open"):
        opening_fees = conn.execute(f"SELECT COALESCE(SUM({fee_usdt}), 0) FROM fills WHERE ts <= ?",
                                    (opened + OPENING_WINDOW_MS,)).fetchone()[0]
    events = conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall()
    last_eq = snaps[-1][1] if snaps else None
    return {"opened_at": opened, "start_equity": start_eq, "equity": last_eq,
            "change": (last_eq - start_eq) if (last_eq is not None and start_eq is not None) else None,
            "opening_fees": float(opening_fees),
            "change_after_costs": (last_eq - start_eq - float(opening_fees))
            if (last_eq is not None and start_eq is not None) else None,
            "funding": float(funding), "fees_usdt": float(fees_usdt), "drawdown_usdt": dd,
            "outside_share": (outside / total) if total else 0.0, "events": dict(events),
            "halted": state.get("halted")}


def model_funding(opened_ms: int, now_ms: int, syms: List[str], notional_usdt: float, api=None) -> float:
    """
    Сумма опубликованных ставок Bybit за (opened; now] × номинал — расчётный фандинг шорта. Биржа отдаёт
    не больше 200 записей за запрос — листаем назад по endTime, как history.sync_funding. До 29.09 был один
    запрос: за 12 недель у монеты 252 выплаты, терялась пятая часть, и проверка «получено ≥ 60 %
    расчётного» завышалась в ≈1,26 раза.
    """
    if api is None:
        from backtest import history
        api = history.BybitHistory()
    total = 0.0
    for s in syms:
        rates: Dict[int, float] = {}
        end = now_ms
        while True:
            rows = api.get("/v5/market/funding/history", {"category": "linear", "symbol": s,
                                                          "endTime": end, "limit": FUNDING_PAGE}).get("list") or []
            stamps = [int(r["fundingRateTimestamp"]) for r in rows]
            for ts, r in zip(stamps, rows):
                if opened_ms < ts <= now_ms:
                    rates[ts] = float(r["fundingRate"])
            if len(rows) < FUNDING_PAGE or min(stamps) <= opened_ms:
                break
            end = min(stamps) - 1
        total += sum(rates.values()) * notional_usdt
    return total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Отчёт И13")
    parser.add_argument("--model", action="store_true", help="расчётный фандинг по опубликованным ставкам")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(str(carry_dir() / "carry.db"))
    s = summary(conn)
    if not s["opened_at"]:
        print("И13: пары ещё не открыты;", s["events"])
        return 0
    days = (time.time() * 1000 - s["opened_at"]) / DAY
    print(f"И13: {days:.1f} дн. с открытия; стоимость {s['equity']:.2f} против старта {s['start_equity']:.2f} "
          f"({s['change']:+.2f} USDT; после комиссий открытия {s['opening_fees']:.2f} — {s['change_after_costs']:+.2f}); фандинг {s['funding']:+.2f} USDT; комиссии {s['fees_usdt']:.2f} USDT; "
          f"просадка {s['drawdown_usdt']:.2f} USDT; часов с хеджем вне ±5 % {100 * s['outside_share']:.2f} %; "
          f"события {s['events']}; {'ОСТАНОВЛЕН: ' + s['halted'] if s['halted'] else 'работает'}")
    if args.model:
        m = model_funding(s["opened_at"], int(time.time() * 1000), symbols(), notional())
        print(f"расчётный фандинг по опубликованным ставкам: {m:+.2f} USDT; получено {s['funding']:+.2f} "
              f"({100 * s['funding'] / m:.0f} % расчётного)" if m else "расчётный фандинг: 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
