"""
Своя база И13 (SQLite в томе /carry): состояние, исполнения, начисления фандинга, часовые снимки,
события. Демо-биржа хранит ордера 7 дней — всё нужное для итога сохраняется здесь.
"""
import json
import sqlite3
from typing import Dict, Iterable, Optional

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)",
    "CREATE TABLE IF NOT EXISTS fills (exec_id TEXT PRIMARY KEY, category TEXT, symbol TEXT, side TEXT,"
    " qty REAL, price REAL, fee REAL, fee_coin TEXT, ts INTEGER)",
    "CREATE TABLE IF NOT EXISTS funding (id TEXT PRIMARY KEY, symbol TEXT, change REAL, funding REAL, ts INTEGER)",
    "CREATE TABLE IF NOT EXISTS snapshots (ts INTEGER PRIMARY KEY, equity REAL, mm_rate REAL, deviations TEXT)",
    "CREATE TABLE IF NOT EXISTS events (ts INTEGER, kind TEXT, detail TEXT)",
)


def _f(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class Store:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        for sql in SCHEMA:
            self.conn.execute(sql)
        # База, созданная до 14.09 (без позиций в снимке): колонка добавляется на месте.
        if "positions" not in {r[1] for r in self.conn.execute("PRAGMA table_info(snapshots)")}:
            self.conn.execute("ALTER TABLE snapshots ADD COLUMN positions TEXT")
        self.conn.commit()

    def get(self, key: str) -> Optional[str]:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value) -> None:
        self.conn.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (key, str(value)))
        self.conn.commit()

    def add_fills(self, category: str, rows: Iterable[Dict]) -> int:
        added = 0
        for r in rows:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO fills (exec_id, category, symbol, side, qty, price, fee, fee_coin, ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (r["execId"], category, r.get("symbol"), r.get("side"), _f(r.get("execQty")), _f(r.get("execPrice")),
                 _f(r.get("execFee")), r.get("feeCurrency") or "", int(r.get("execTime") or 0)))
            added += cur.rowcount
        self.conn.commit()
        return added

    def add_funding(self, rows: Iterable[Dict]) -> int:
        """Начисления фандинга; итог считается по change — изменению кошелька (знак однозначный)."""
        added = 0
        for r in rows:
            key = r.get("id") or f"{r.get('symbol')}:{r.get('transactionTime')}"
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO funding (id, symbol, change, funding, ts) VALUES (?, ?, ?, ?, ?)",
                (key, r.get("symbol"), _f(r.get("change")), _f(r.get("funding")), int(r.get("transactionTime") or 0)))
            added += cur.rowcount
        self.conn.commit()
        return added

    def snapshot(self, ts: int, equity: float, mm_rate: Optional[float], deviations: Dict[str, float],
                 positions: Optional[Dict[str, dict]] = None) -> None:
        """positions — {символ: {"spot": объём спота, "short": объём шорта, "price": цена}} для мини-аппа."""
        self.conn.execute("INSERT OR REPLACE INTO snapshots (ts, equity, mm_rate, deviations, positions) VALUES (?, ?, ?, ?, ?)",
                          (ts, equity, mm_rate, json.dumps(deviations), json.dumps(positions or {})))
        self.conn.commit()

    def event(self, ts: int, kind: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO events (ts, kind, detail) VALUES (?, ?, ?)", (ts, kind, detail[:500]))
        self.conn.commit()
