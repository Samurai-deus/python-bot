"""
Поправка учёта исполнителя при внешнем движении средств на его счёте (демо-средства, перевод, сделка вне
правила): результат «стоимость − start_equity» и просадка от пика должны отражать только торговлю.
Кейс 15–16.09.2026: проба apply_demo_usdt(1000) на субсчёте И18 показалась в отчёте как +18,9 % капитала.

    python -m portfolio.adjust --db /btcalts/btcalts.db --amount 1000 --reason "проба демо-средств 15.09"
    python -m portfolio.adjust --db /portfolio/portfolio.db --amount -500 --reason "..." --at 1789474594537

Сдвигаются start_equity, снимки стоимости до момента движения (--at, по умолчанию сейчас) и peak_equity — живой
максимум для остановки по просадке — только при движении «сейчас» (при позднем --at неизвестно, до пика оно было
или после; тогда peak_equity проверить глазами). Пишется событие adjust; та же причина второй раз не применяется.
Базы И14/И18 (portfolio.store) и И13 (carry.store): таблицы state / snapshots(ts, equity) / events у них общие.
"""
import argparse
import sqlite3
import time
from typing import Optional

KIND = "adjust"


def external_flow(conn: sqlite3.Connection, amount: float, reason: str, now_ms: int, at_ms: Optional[int] = None) -> dict:
    """Применить поправку на amount USDT. Возвращает {snapshots, start_equity, peak_equity, applied}."""
    if conn.execute("SELECT 1 FROM events WHERE kind = ? AND detail LIKE ?", (KIND, f"%[{reason}]%")).fetchone():
        return {"applied": False, "snapshots": 0, "start_equity": None, "peak_equity": None}
    at = now_ms if at_ms is None else at_ms
    start = conn.execute("SELECT value FROM state WHERE key = 'start_equity'").fetchone()
    new_start = float(start[0]) + amount if start else None
    if new_start is not None:
        conn.execute("UPDATE state SET value = ? WHERE key = 'start_equity'", (str(new_start),))
    moved = conn.execute("UPDATE snapshots SET equity = equity + ? WHERE ts < ?", (amount, at)).rowcount
    new_peak = None
    if at_ms is None:
        peak = conn.execute("SELECT value FROM state WHERE key = 'peak_equity'").fetchone()
        if peak:
            new_peak = float(peak[0]) + amount
            conn.execute("UPDATE state SET value = ? WHERE key = 'peak_equity'", (str(new_peak),))
    detail = (f"внешнее движение {amount:+.2f} USDT [{reason}]: start_equity → {new_start}, снимков до {at}: {moved}, "
              f"peak_equity → {new_peak if new_peak is not None else 'не тронут'}")
    conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (now_ms, KIND, detail[:500]))
    conn.commit()
    return {"applied": True, "snapshots": moved, "start_equity": new_start, "peak_equity": new_peak}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="поправка учёта исполнителя при внешнем движении средств")
    ap.add_argument("--db", required=True, help="база исполнителя (portfolio.db / btcalts.db / carry.db)")
    ap.add_argument("--amount", type=float, required=True, help="USDT: + приток, − отток")
    ap.add_argument("--reason", required=True, help="причина; повтор той же причины не применяется")
    ap.add_argument("--at", type=int, default=None, help="момент движения, мс UTC (по умолчанию сейчас)")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(a.db)
    try:
        r = external_flow(conn, a.amount, a.reason, int(time.time() * 1000), a.at)
    finally:
        conn.close()
    if not r["applied"]:
        print(f"уже применено: [{a.reason}]")
        return 1
    print(f"start_equity → {r['start_equity']}; снимков сдвинуто {r['snapshots']}; peak_equity → {r['peak_equity']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
