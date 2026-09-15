"""Отчёт И18: `python -m btcalts.report` — старт, стоимость против старта, просадка, ребалансировки, фандинг, позиции, события."""
import json
import sqlite3
import time
from datetime import UTC, datetime

from btcalts.__main__ import capital, root_dir


def runs_by_monday(conn) -> dict:
    """Число прогонов на понедельник из журнала rebalance_runs (в старых базах таблицы нет — пусто)."""
    try:
        return dict(conn.execute("SELECT t, COUNT(*) FROM rebalance_runs GROUP BY t").fetchall())
    except sqlite3.OperationalError:
        return {}


def main() -> int:
    conn = sqlite3.connect(str(root_dir() / "btcalts.db"))
    state = dict(conn.execute("SELECT key, value FROM state").fetchall())
    if not state.get("started_at"):
        print("И18: ещё не стартовал;", dict(conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall()))
        return 0
    start_eq = float(state["start_equity"])
    snaps = conn.execute("SELECT ts, equity, positions FROM snapshots ORDER BY ts").fetchall()
    peak, dd = start_eq, 0.0
    for _, eq, _ in snaps:
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    last_ts, last_eq, last_pos = snaps[-1] if snaps else (0, start_eq, "{}")
    days = (time.time() * 1000 - int(state["started_at"])) / 86_400_000
    print(f"И18: {days:.1f} дн. со старта; стоимость {last_eq:.2f} против {start_eq:.2f} "
          f"({last_eq - start_eq:+.2f} USDT, {100 * (last_eq - start_eq) / capital():+.2f} % капитала); "
          f"просадка от пика {dd:.2f} USDT ({100 * dd / capital():.1f} %); {'ОСТАНОВЛЕН: ' + state['halted'] if state.get('halted') else 'работает'}")
    runs = runs_by_monday(conn)
    for t, done_ms, weights, orders, failed in conn.execute("SELECT * FROM rebalances ORDER BY t"):
        w = json.loads(weights)
        print(f"  {datetime.fromtimestamp(t / 1000, UTC):%d.%m} ребалансировка: BTC {w.get('BTCUSDT', 0):+.2f}, альтов {sum(1 for v in w.values() if v < 0)}, "
              f"ордеров {len(json.loads(orders))}, не прошло {len(json.loads(failed))}" + (f", прогонов {runs[t]}" if runs.get(t, 1) > 1 else ""))
    funding = conn.execute("SELECT COALESCE(SUM(change), 0) FROM funding WHERE ts >= ?", (int(state["started_at"]),)).fetchone()[0]
    pos = json.loads(last_pos or "{}")
    print(f"  фандинг {float(funding):+.2f} USDT; позиций {len(pos)}, валовая {sum(abs(v) for v in pos.values()):.0f} USDT")
    print("  события:", dict(conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind").fetchall()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
