"""
Данные исследовательской программы для мини-аппа (docs/TRADER_PLAN.md): портфель И14, сбор
фандинга И13, запись стакана, сборщик новостей, наблюдение И4/И3. Всё читается из баз и файлов
исполнителей (тома подключены в API только на чтение) и базы бота — к бирже API не обращается.
Сроки и критерии — константы PROGRAM, они повторяют план; правило меняется — менять здесь.
"""
import json
import os
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

DAY_MS = 86_400_000
WEEK_MS = 7 * DAY_MS

PROGRAM: Dict[str, Any] = {
    "portfolio": {
        "title": "И14 — рисковый портфель: тренд И4 + моментум И3 + продолжение И17а (с 21.09)",
        "risk": "40 % годовых (И4 ×1,61, И3 ×0,78, И17а ×0,48 с 21.09); на увиденной истории ≈ +28 %/год, просадка 30 %",
        "start": "2026-09-14", "end": "2026-12-07", "verdict": "2026-12-14",
        "rebalance": "понедельник 00:02 UTC",
        "criteria": [
            "исполнение не хуже бумаги (наблюдение И4/И3) минус 1,5 % капитала — сравнение с 21.09",
            "стоп по правилу не сработал (просадка от пика > 25 % капитала)",
            "недель с непройденной ребалансировкой — не больше 1",
        ],
        "notes": ["позиции меньше лота биржи не открываются (BTC 0,001 ≈ 77 $)"],
    },
    "carry": {
        "title": "И13 — сбор фандинга: спот LONG + бессрочный SHORT, BTC и ETH",
        "notional": "по 10 000 USDT на монету, плечо 1×, без ротации; отдельный демо-субсчёт",
        "start": "2026-09-14", "end": "2026-12-07", "verdict": "2026-12-14",
        "criteria": [
            "итог срока после всех издержек выше 0",
            "полученный фандинг ≥ 60 % расчётного по опубликованным ставкам",
            "просадка ≤ 3 % капитала позиции",
            "ни одной ликвидации и экстренного закрытия",
            "хедж вне ±5 % — меньше 1 % времени",
        ],
    },
    "recorder": {"title": "Запись стакана и сделок Bybit", "symbols": "BTC, ETH, SOL, XRP, DOGE, BNB, XAU",
                 "hypotheses_from": "2026-10-11", "cap_gb": 20},
    "news": {"title": "И10 / И10б — новости с оценкой ИИ, с названием монеты и без (только вперёд)",
             "first_check": "2026-12-06",
             "budget_usd": 0.3, "signal": "новизна, сила ≥ 2, уверенность ≥ 0,6, направление; ≥ 100 сигналов"},
    "watch": {"title": "Наблюдение вперёд на бумаге: И4 (тренд, N = 30) и И3 (моментум, L = 28)",
              "start": "2026-09-14", "first_check": "2026-12-14", "every_weeks": 13,
              "stop": "средняя < −2 SE или просадка > 15 % (И4) / > 25 % (И3); через 26 недель без остановки и средняя ≥ 0 → демо",
              "note": "сигналы те же, что у И14; наблюдение судит, есть ли эффект, И14 — доходит ли он до счёта"},
}


def _ro(path: str) -> Optional[sqlite3.Connection]:
    if not path or not Path(path).is_file():
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)


def _iso(ms) -> Optional[str]:
    return None if not ms else datetime.fromtimestamp(int(ms) / 1000, UTC).isoformat()


def read_portfolio(path: str, capital: float, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = _ro(path)
    if conn is None:
        return None
    now_ms = now_ms or int(time.time() * 1000)
    try:
        state = dict(conn.execute("SELECT key, value FROM state").fetchall())
        snaps = conn.execute("SELECT ts, equity, positions FROM snapshots ORDER BY ts").fetchall()
        rebs = conn.execute("SELECT t, done_ms, weights, orders, failed FROM rebalances ORDER BY t").fetchall()
        events = conn.execute("SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 10").fetchall()
    finally:
        conn.close()
    started = int(state["started_at"]) if state.get("started_at") else None
    start_eq = float(state["start_equity"]) if state.get("start_equity") else None
    peak, dd = start_eq or 0.0, 0.0
    for ts, eq, _ in snaps:
        if started is None or ts < started:
            continue
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    last_ts, last_eq, last_pos = snaps[-1] if snaps else (None, None, "{}")
    positions = json.loads(last_pos or "{}")
    last_reb = int(state["last_rebalance_t"]) if state.get("last_rebalance_t") else None
    return {
        "status": "halted" if state.get("halted") else ("running" if started else "waiting"),
        "halted_reason": state.get("halted"),
        "started_at": _iso(started), "snapshot_at": _iso(last_ts),
        "capital": capital,
        "start_equity": start_eq, "equity": last_eq,
        "change": (last_eq - start_eq) if (last_eq is not None and start_eq is not None) else None,
        "drawdown": dd,
        "gross": sum(abs(v) for v in positions.values()),
        "positions": [{"symbol": s, "side": "LONG" if v > 0 else "SHORT", "notional": abs(v), "weight": abs(v) / capital}
                      for s, v in sorted(positions.items(), key=lambda kv: -abs(kv[1]))],
        "next_rebalance": _iso(last_reb + WEEK_MS) if last_reb else None,
        "rebalances": [{"t": _iso(t), "done_at": _iso(d), "coins": len(json.loads(w)), "orders": len(json.loads(o)),
                        "failed": len(json.loads(f))} for t, d, w, o, f in rebs],
        "events": [{"at": _iso(ts), "kind": k, "detail": d} for ts, k, d in events],
    }


def read_carry(path: str, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = _ro(path)
    if conn is None:
        return None
    try:
        state = dict(conn.execute("SELECT key, value FROM state").fetchall())
        cols = {r[1] for r in conn.execute("PRAGMA table_info(snapshots)")}
        sel = "ts, equity, mm_rate, deviations" + (", positions" if "positions" in cols else ", NULL")
        snaps = conn.execute(f"SELECT {sel} FROM snapshots ORDER BY ts").fetchall()
        opened = int(state["opened_at"]) if state.get("opened_at") else 0
        funding = conn.execute("SELECT COALESCE(SUM(change), 0) FROM funding WHERE ts >= ?", (opened,)).fetchone()[0]
        fees = conn.execute("SELECT COALESCE(SUM(fee * CASE WHEN fee_coin IN ('USDT', '') THEN 1 ELSE price END), 0)"
                            " FROM fills WHERE ts >= ?", (opened,)).fetchone()[0]
        events = conn.execute("SELECT ts, kind, detail FROM events ORDER BY ts DESC LIMIT 10").fetchall()
    finally:
        conn.close()
    start_eq = float(state["start_equity"]) if state.get("start_equity") else None
    peak, dd, outside, total = None, 0.0, 0, 0
    for ts, eq, _, devs, _ in snaps:
        if not opened or ts < opened:
            continue
        peak = eq if peak is None else max(peak, eq)
        dd = max(dd, peak - eq)
        for v in json.loads(devs or "{}").values():
            total += 1
            outside += v > 0.05
    last = snaps[-1] if snaps else None
    positions = json.loads((last[4] if last else None) or "{}")
    deviations = json.loads((last[3] if last else None) or "{}")
    return {
        "status": "halted" if state.get("halted") else ("running" if opened else "waiting"),
        "halted_reason": state.get("halted"),
        "opened_at": _iso(opened), "snapshot_at": _iso(last[0]) if last else None,
        "start_equity": start_eq, "equity": last[1] if last else None,
        "change": (last[1] - start_eq) if (last and start_eq is not None) else None,
        "funding": float(funding), "fees": float(fees), "drawdown": dd,
        "mm_rate": last[2] if last else None, "outside_share": (outside / total) if total else 0.0,
        "positions": [{"symbol": s, "spot": p.get("spot", 0.0), "short": p.get("short", 0.0), "price": p.get("price"),
                       "notional": p.get("spot", 0.0) * (p.get("price") or 0), "deviation": deviations.get(s)}
                      for s, p in sorted(positions.items())],
        "events": [{"at": _iso(ts), "kind": k, "detail": d} for ts, k, d in events],
    }


def read_recorder(root: str, now_ms: Optional[int] = None) -> Optional[Dict[str, Any]]:
    base = Path(root) if root else None
    if base is None or not base.is_dir():
        return None
    now_ms = now_ms or int(time.time() * 1000)
    events: List[dict] = []
    ev = base / "events.jsonl"
    if ev.is_file():
        events = [json.loads(x) for x in ev.read_text(encoding="utf-8").splitlines() if x.strip()]
    kinds: Dict[str, int] = {}
    for e in events:
        kinds[e.get("event", "?")] = kinds.get(e.get("event", "?"), 0) + 1
    size = sum(p.stat().st_size for p in base.rglob("*.jsonl.gz"))
    first = events[0]["t"] if events else None
    hours = (now_ms - first) / 3_600_000 if first else 0
    try:
        heartbeat = float((base / "heartbeat").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        heartbeat = None
    return {
        "since": _iso(first), "events": kinds, "gaps": kinds.get("gap", 0),
        "size_mb": size / 1e6, "mb_per_day": (size / 1e6 / hours * 24) if hours else None,
        "last_message_age_s": (now_ms / 1000 - heartbeat) if heartbeat else None,
        "last_event": events[-1] if events else None,
    }


def read_news(day: str) -> Dict[str, Any]:
    """Сборщик новостей — из базы бота (news_items/news_scores, расход в ai_usage)."""
    import database
    conn = database.get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM news_items")
        total = cur.fetchone()["n"]
        cur.execute("SELECT score_status AS s, COUNT(*) AS n FROM news_items WHERE backlog = 0 GROUP BY score_status")
        fresh = {str(r["s"]): r["n"] for r in cur.fetchall()}
        cur.execute("SELECT COUNT(*) AS n FROM news_scores WHERE novelty = 1 AND magnitude >= 2 AND confidence >= 0.6 "
                    "AND direction IN ('up', 'down')")
        signals = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) AS n FROM news_scores")
        rows = cur.fetchone()["n"]
        try:  # таблицу И10б создаёт сборщик; до его первого цикла на новом релизе её может не быть
            cur.execute("SELECT status AS s, COUNT(*) AS n FROM news_blind GROUP BY status")
            blind = {str(r["s"]): r["n"] for r in cur.fetchall()}
        except Exception:
            blind = {}
        conn.commit()
    except Exception:
        return {"items": 0, "fresh": {}, "signals": 0, "score_rows": 0, "blind": {}, "spend_today": None}
    finally:
        conn.close()
    try:
        spend = database.get_ai_spend(day, purpose="news")
    except Exception:
        spend = None
    return {"items": total, "fresh": fresh, "signals": signals, "score_rows": rows, "blind": blind, "spend_today": spend}


def overview(now: Optional[float] = None) -> Dict[str, Any]:
    now = now or time.time()
    now_ms = int(now * 1000)
    capital = float(os.environ.get("PORTFOLIO_CAPITAL_USDT", "1000"))
    return {
        "generated_at": datetime.fromtimestamp(now, UTC).isoformat(),
        "program": PROGRAM,
        "portfolio": read_portfolio(os.environ.get("RESEARCH_PORTFOLIO_DB", "/portfolio/portfolio.db"), capital, now_ms),
        "carry": read_carry(os.environ.get("RESEARCH_CARRY_DB", "/carry/carry.db"), now_ms),
        "recorder": read_recorder(os.environ.get("RESEARCH_RECORDER_DIR", "/recorder"), now_ms),
        "news": read_news(datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%d")),
    }
