"""Своя база И14 (SQLite в томе /portfolio): состояние, ребалансировки, снимки, фандинг, события."""
import json
import sqlite3
from typing import Dict, Iterable, Optional

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS rebalances (t INTEGER PRIMARY KEY, done_ms INTEGER, weights TEXT, orders TEXT, failed TEXT)",
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

    def get(self, key: str) -> Optional[str]:
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

    def rebalance(self, t: int, done_ms: int, weights: Dict[str, float], orders: list, failed: list) -> None:
        self.conn.execute("INSERT OR REPLACE INTO rebalances (t, done_ms, weights, orders, failed) VALUES (?, ?, ?, ?, ?)",
                          (t, done_ms, json.dumps(weights), json.dumps(orders), json.dumps(failed)))
        self.conn.commit()

    def has_rebalance(self, t: int) -> bool:
        return self.conn.execute("SELECT 1 FROM rebalances WHERE t = ?", (t,)).fetchone() is not None

    def snapshot(self, ts: int, equity: float, positions: Dict[str, float]) -> None:
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
