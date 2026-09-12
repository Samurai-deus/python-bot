"""
Дневные ряды традиционных рынков для И8 (docs/TRADER_PLAN.md) — в таблицу macro_daily кэша истории.

    py -m backtest.macro_data --dir <каталог с файлами>     # разобрать скачанные файлы
    py -m backtest.macro_data                               # скачать и разобрать

FRED (CSV): Nasdaq Composite (NASDAQCOM), курсы JPY за USD (DEXJPUS), USD за GBP (DEXUSUK),
USD за EUR (DEXUSEU). LBMA (JSON): золото PM и серебро, цена в USD — первый элемент «v».
Месячные средние (Всемирный банк, datahub) не используются: усреднение создаёт ложный тренд.
"""
import argparse
import csv
import io
import json
import pathlib
import sqlite3
from typing import List, Tuple

from backtest import history

FRED = {"NASDAQ": "NASDAQCOM", "USDJPY": "DEXJPUS", "GBPUSD": "DEXUSUK", "EURUSD": "DEXUSEU"}
LBMA = {"GOLD": "gold_pm", "SILVER": "silver"}
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
LBMA_URL = "https://prices.lbma.org.uk/json/{}.json"


def parse_fred(text: str, sid: str) -> List[Tuple[str, float]]:
    """Строки (день, значение); пропуски FRED («.» или пусто) выбрасываются."""
    out = []
    for rec in csv.DictReader(io.StringIO(text)):
        v = (rec.get(sid) or "").strip()
        if v and v != ".":
            out.append((rec["observation_date"], float(v)))
    return out


def parse_lbma(text: str) -> List[Tuple[str, float]]:
    """Строки (день, цена в USD) из JSON LBMA: [{"d": "ГГГГ-ММ-ДД", "v": [USD, GBP, EUR]}, ...]."""
    return [(rec["d"], float(rec["v"][0])) for rec in json.loads(text) if rec.get("v") and rec["v"][0]]


def connect(db=history.DEFAULT_DB) -> sqlite3.Connection:
    conn = history.connect(db)
    conn.execute("CREATE TABLE IF NOT EXISTS macro_daily (series TEXT NOT NULL, day TEXT NOT NULL, value REAL,"
                 " PRIMARY KEY (series, day))")
    return conn


def store(conn, series: str, rows: List[Tuple[str, float]]) -> int:
    before = conn.total_changes
    conn.executemany("INSERT OR REPLACE INTO macro_daily VALUES (?, ?, ?)", [(series, d, v) for d, v in rows])
    conn.commit()
    return conn.total_changes - before


def _download(url: str) -> str:
    import requests
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            return r.text
        except Exception:
            if attempt == 2:
                raise
    raise RuntimeError(url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Дневные ряды традиционных рынков в кэш истории")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--dir", help="каталог с fred-<id>.csv и lbma-<id>.json вместо скачивания")
    args = parser.parse_args(argv)
    conn = connect(args.db)
    base = pathlib.Path(args.dir) if args.dir else None
    for name, sid in FRED.items():
        text = (base / f"fred-{sid}.csv").read_text(encoding="utf-8") if base else _download(FRED_URL.format(sid))
        print(name, "строк:", store(conn, name, parse_fred(text, sid)))
    for name, fid in LBMA.items():
        text = (base / f"lbma-{fid}.json").read_text(encoding="utf-8") if base else _download(LBMA_URL.format(fid))
        print(name, "строк:", store(conn, name, parse_lbma(text)))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
