"""Своя база И14 (SQLite в томе /portfolio): состояние, ребалансировки, снимки, фандинг, события."""
import json
import sqlite3
from collections.abc import Iterable
import builtins

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS rebalances (t INTEGER PRIMARY KEY, done_ms INTEGER, weights TEXT, orders TEXT, failed TEXT)",
    # Журнал всех прогонов: 14.09 второй прогон недели запуска перезаписал запись понедельника (ключ — t).
    "CREATE TABLE IF NOT EXISTS rebalance_runs (t INTEGER, done_ms INTEGER, weights TEXT, orders TEXT, failed TEXT,"
    " PRIMARY KEY (t, done_ms))",
    "CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, equity REAL, positions TEXT)",
    "CREATE TABLE IF NOT EXISTS funding (id TEXT PRIMARY KEY, symbol TEXT, change REAL, ts INTEGER)",
    "CREATE TABLE IF NOT EXISTS events (ts INTEGER, kind TEXT, detail TEXT)",
)


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        for sql in SCHEMA:
            self.conn.execute(sql)
        self.conn.commit()

    def get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def keys(self, prefix: str) -> list:
        """Ключи состояния с префиксом (например, blocked:) — без префикса."""
        rows = self.conn.execute("SELECT key FROM state WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return [r[0][len(prefix):] for r in rows]

    def set(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (key, str(value)))
        self.conn.commit()

    def rebalance(self, t: int, done_ms: int, weights: dict[str, float], orders: list, failed: list) -> None:
        row = (t, done_ms, json.dumps(weights), json.dumps(orders), json.dumps(failed))
        self.conn.execute("INSERT OR REPLACE INTO rebalances (t, done_ms, weights, orders, failed) VALUES (?, ?, ?, ?, ?)", row)
        self.conn.execute("INSERT OR IGNORE INTO rebalance_runs (t, done_ms, weights, orders, failed) VALUES (?, ?, ?, ?, ?)", row)
        self.conn.commit()

    def last_weights(self) -> dict[str, float] | None:
        """Цели последнего прогона ребалансировки; None — ребалансировок ещё не было."""
        row = self.conn.execute("SELECT weights FROM rebalance_runs ORDER BY done_ms DESC LIMIT 1").fetchone()
        return json.loads(row[0]) if row else None

    def traded_symbols(self) -> builtins.set[str]:      # в теле класса `set` — это метод Store.set
        """Все монеты из целей и ордеров журнала прогонов — чем исполнитель когда-либо торговал."""
        out = set()
        for w, o in self.conn.execute("SELECT weights, orders FROM rebalance_runs"):
            out |= set(json.loads(w)) | {x[0] for x in json.loads(o)}
        return out

    def week_retry(self, t: int) -> tuple[dict[str, float], builtins.set[str]] | None:
        """
        Повтор недели t: цели последнего её прогона и монеты, где ордер тогда не прошёл. None — прогонов
        недели ещё не было. Цели не пересчитываются: повтор доводит те же, а не новые по сдвинувшимся ценам.
        """
        row = self.conn.execute("SELECT weights, failed FROM rebalance_runs WHERE t = ? ORDER BY done_ms DESC LIMIT 1",
                                (t,)).fetchone()
        if row is None:
            return None
        return json.loads(row[0]), {f[0] for f in json.loads(row[1])}

    def has_rebalance(self, t: int) -> bool:
        return self.conn.execute("SELECT 1 FROM rebalances WHERE t = ?", (t,)).fetchone() is not None

    def snapshot(self, ts: int, equity: float, positions: dict[str, float]) -> None:
        self.conn.execute("INSERT OR REPLACE INTO snapshots (ts, equity, positions) VALUES (?, ?, ?)",
                          (ts, equity, json.dumps(positions)))
        self.conn.commit()

    def add_funding(self, rows: Iterable[dict]) -> int:
        added = 0
        for r in rows:
            key = r.get("id") or f"{r.get('symbol')}:{r.get('transactionTime')}"
            cur = self.conn.execute("INSERT OR IGNORE INTO funding (id, symbol, change, ts) VALUES (?, ?, ?, ?)",
                                    (key, r.get("symbol"), float(r.get("change") or 0), int(r.get("transactionTime") or 0)))
            added += cur.rowcount
        self.conn.commit()
        return added

    def event(self, ts: int, kind: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (ts, kind, detail[:500]))
        self.conn.commit()
