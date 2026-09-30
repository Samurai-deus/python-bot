"""
И10 / И10б — новости с оценкой ИИ, проверка вперёд (docs/TRADER_PLAN.md). Записано и заморожено 30.09.2026 —
до первой проверки 06.12 и без просмотра результата (аудит 29.09: кода оценки не было, его пришлось бы писать,
уже видя данные, и выбор деталей расчёта стал бы свободой аналитика).

    py -m backtest.h10 --db <market_bot.db> [--cutoff 2026-12-06] [--out h10.json]

Правило — как записано в плане; ниже только то, что план оставлял открытым (уточнения записаны в плане тем же
числом, до данных):
• Замороженная оценка — prompt_version news-v1 (И10) / news-blind-v1 (И10б) и модель с префиксом
  anthropic/claude-sonnet-5. Оценки другой модели или подсказки в проверку не входят и считаются в отчёте.
• Контракт монеты — ТИКЕРUSDT, иначе 1000ТИКЕРUSDT, бессрочный на Bybit и запущенный к моменту оценки.
• Вход — открытие первой минуты, начинающейся не раньше чем через 1 мин после оценки; выход — открытие минуты
  через 1 ч (4 ч) после входа. Сделка без свечи входа или выхода в проверку не входит и считается в отчёте.
• Результат сделки — доля номинала: сторона × (выход / вход − 1) − 0,31 % издержек на круг.
• Повторы — одна монета: сигнал позже чем через час после последнего ПРИНЯТОГО сигнала этой монеты.
• В набор проверки входят сигналы, у которых выход через 4 ч не позже даты проверки — оба варианта выхода
  на одном наборе.
• Два варианта выхода → интервал 97,5 %; гипотеза проходит, если все критерии выполнены хотя бы в одном
  варианте. Бутстреп — по дням входа (UTC), 4000 повторов, зерно 7.
• Три отрезка — равные по времени трети окна [старт, дата проверки]; «плюс» — средняя сделка отрезка > 0
  (пустой отрезок — не плюс).
• Парное сравнение И10б − И10 — на заголовках, оценённых обеими ветками (вторая оценка ok): сделки каждой
  ветки на этих заголовках, разница средних; дни — по моменту первой оценки. Два варианта выхода → 97,5 %
  (строже записанных 95 %, по той же поправке, что основные критерии); подтверждение — нижняя граница > 0
  хотя бы в одном варианте.
"""
import argparse
import json
import random
import sqlite3
import statistics as st
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS
WEEK_MS = 7 * DAY_MS

# --- правило (заморожено 30.09.2026) -------------------------------------------------------------------
MODEL_PREFIX = "anthropic/claude-sonnet-5"
PROMPT_I10 = "news-v1"
PROMPT_I10B = "news-blind-v1"
MIN_MAGNITUDE = 2
MIN_CONFIDENCE = 0.6
ENTRY_DELAY_MS = MINUTE_MS
EXITS_MS = {"1h": HOUR_MS, "4h": 4 * HOUR_MS}
COST = 0.0031                    # 0,11 % комиссии + 2 × 0,1 % проскальзывания на круг
REPEAT_MS = HOUR_MS
MIN_SIGNALS = 100
MIN_MEAN = 0.002                 # средняя сделка ≥ +0,2 % номинала
MIN_POSITIVE_THIRDS = 2
ALPHA = 0.025                    # два варианта выхода → 97,5 %
MIN_DOWN_SIGNALS = 50
FIRST_CHECK = "2026-12-06"
CHECK_STEP_DAYS = 28
MAX_WEEKS = 26
N_BOOT = 4000
SEED = 7


@dataclass(frozen=True)
class Signal:
    uid: str
    ticker: str
    side: int          # +1 up, −1 down
    t_ms: int          # момент оценки, по которой сигнал
    first_ms: int      # момент первой оценки заголовка (И10) — день для парного сравнения


def day_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


# --- сигналы ------------------------------------------------------------------------------------------
_SIDE = {"up": 1, "down": -1}


def load_i10(conn: sqlite3.Connection) -> list[Signal]:
    rows = conn.execute(
        "SELECT i.uid, s.symbol, s.direction, i.scored_ms FROM news_items i JOIN news_scores s ON s.uid = i.uid "
        "WHERE i.backlog = 0 AND i.score_status = 'ok' AND i.prompt_version = ? AND i.model LIKE ? "
        "AND s.novelty = 1 AND s.magnitude >= ? AND s.confidence >= ? AND s.direction IN ('up', 'down')",
        (PROMPT_I10, MODEL_PREFIX + "%", MIN_MAGNITUDE, MIN_CONFIDENCE)).fetchall()
    return sorted((Signal(u, t, _SIDE[d], ms, ms) for u, t, d, ms in rows), key=lambda x: (x.t_ms, x.ticker))


def load_i10b(conn: sqlite3.Connection) -> list[Signal]:
    """Монеты — из первой (зрячей) оценки заголовка, суждение — из второй (обезличенной)."""
    rows = conn.execute(
        "SELECT b.uid, s.symbol, b.direction, b.scored_ms, i.scored_ms FROM news_blind b "
        "JOIN news_items i ON i.uid = b.uid JOIN news_scores s ON s.uid = b.uid "
        "WHERE b.status = 'ok' AND b.prompt_version = ? AND b.model LIKE ? "
        "AND i.backlog = 0 AND i.score_status = 'ok' AND i.prompt_version = ? AND i.model LIKE ? "
        "AND b.novelty = 1 AND b.magnitude >= ? AND b.confidence >= ? AND b.direction IN ('up', 'down')",
        (PROMPT_I10B, MODEL_PREFIX + "%", PROMPT_I10, MODEL_PREFIX + "%", MIN_MAGNITUDE, MIN_CONFIDENCE)).fetchall()
    return sorted((Signal(u, t, _SIDE[d], ms, first) for u, t, d, ms, first in rows), key=lambda x: (x.t_ms, x.ticker))


def both_scored(conn: sqlite3.Connection) -> set[str]:
    """Заголовки, оценённые обеими ветками (замороженные подсказки и модель)."""
    rows = conn.execute(
        "SELECT b.uid FROM news_blind b JOIN news_items i ON i.uid = b.uid "
        "WHERE b.status = 'ok' AND b.prompt_version = ? AND b.model LIKE ? "
        "AND i.backlog = 0 AND i.score_status = 'ok' AND i.prompt_version = ? AND i.model LIKE ?",
        (PROMPT_I10B, MODEL_PREFIX + "%", PROMPT_I10, MODEL_PREFIX + "%")).fetchall()
    return {r[0] for r in rows}


def start_ms(conn: sqlite3.Connection) -> int | None:
    """Старт И10 — первая оценка замороженной подсказкой и моделью."""
    row = conn.execute("SELECT MIN(scored_ms) FROM news_items WHERE score_status = 'ok' AND prompt_version = ? "
                       "AND model LIKE ?", (PROMPT_I10, MODEL_PREFIX + "%")).fetchone()
    return int(row[0]) if row and row[0] is not None else None


def work_counts(conn: sqlite3.Connection) -> dict:
    """То, что можно смотреть до срока: работа сборщика, не результат."""
    q = lambda sql, args=(): conn.execute(sql, args).fetchone()[0]  # noqa: E731
    return {
        "headlines": q("SELECT COUNT(*) FROM news_items"),
        "backlog": q("SELECT COUNT(*) FROM news_items WHERE backlog = 1"),
        "scored_frozen": q("SELECT COUNT(*) FROM news_items WHERE score_status = 'ok' AND prompt_version = ? AND model LIKE ?",
                           (PROMPT_I10, MODEL_PREFIX + "%")),
        "scored_other": q("SELECT COUNT(*) FROM news_items WHERE score_status = 'ok' AND NOT (prompt_version = ? AND model LIKE ?)",
                          (PROMPT_I10, MODEL_PREFIX + "%")),
        "not_by_schema": q("SELECT COUNT(*) FROM news_items WHERE score_status IS NOT NULL AND score_status != 'ok'"),
        "blind_ok": q("SELECT COUNT(*) FROM news_blind WHERE status = 'ok'"),
        "blind_other": q("SELECT COUNT(*) FROM news_blind WHERE status != 'ok'"),
    }


def dedup(signals: Iterable[Signal]) -> list[Signal]:
    """Одна монета — сигнал позже чем через час после последнего принятого сигнала этой монеты."""
    last: dict[str, int] = {}
    out = []
    for s in sorted(signals, key=lambda x: (x.t_ms, x.ticker, x.uid)):
        if s.ticker in last and s.t_ms - last[s.ticker] < REPEAT_MS:
            continue
        last[s.ticker] = s.t_ms
        out.append(s)
    return out


# --- цены ---------------------------------------------------------------------------------------------
class Market:
    """Контракт монеты на момент t и открытие минутной свечи. Реализация по сети — BybitMarket."""

    def symbol_for(self, ticker: str, t_ms: int) -> str | None:
        raise NotImplementedError

    def minute_open(self, symbol: str, t_ms: int) -> float | None:
        raise NotImplementedError


def entry_minute(t_ms: int) -> int:
    x = t_ms + ENTRY_DELAY_MS
    return -(-x // MINUTE_MS) * MINUTE_MS


def trades(signals: Sequence[Signal], market: Market, exit_ms: int) -> tuple[list[dict], dict]:
    out, skipped = [], {"no_contract": 0, "no_price": 0}
    for s in signals:
        sym = market.symbol_for(s.ticker, s.t_ms)
        if sym is None:
            skipped["no_contract"] += 1
            continue
        t0 = entry_minute(s.t_ms)
        p0, p1 = market.minute_open(sym, t0), market.minute_open(sym, t0 + exit_ms)
        if not p0 or not p1:
            skipped["no_price"] += 1
            continue
        out.append({"uid": s.uid, "ticker": s.ticker, "symbol": sym, "side": s.side, "t_entry": t0,
                    "day": t0 // DAY_MS, "first_day": s.first_ms // DAY_MS,
                    "ret": s.side * (p1 / p0 - 1) - COST})
    return out, skipped


# --- статистика ---------------------------------------------------------------------------------------
def _quantiles(vals: list[float], alpha: float) -> tuple[float, float]:
    vals = sorted(vals)
    lo = vals[int(alpha * len(vals))]
    hi = vals[min(len(vals) - 1, int((1 - alpha) * len(vals)))]
    return lo, hi


def boot_mean(tr: Sequence[dict], alpha: float = ALPHA, key: str = "day", n: int = N_BOOT, seed: int = SEED):
    """Интервал средней сделки: бутстреп по дням (день целиком со всеми его сделками)."""
    by_day: dict[int, list[float]] = {}
    for x in tr:
        by_day.setdefault(x[key], []).append(x["ret"])
    days = sorted(by_day)
    if not days:
        return None
    rnd = random.Random(seed)
    means = []
    for _ in range(n):
        pick = [r for d in rnd.choices(days, k=len(days)) for r in by_day[d]]
        means.append(st.mean(pick))
    return _quantiles(means, alpha)


def thirds_positive(tr: Sequence[dict], start: int, end: int) -> list[float | None]:
    span = (end - start) / 3
    out = []
    for i in range(3):
        a, b = start + i * span, start + (i + 1) * span
        seg = [x["ret"] for x in tr if a <= x["t_entry"] < b]
        out.append(st.mean(seg) if seg else None)
    return out


def criteria(tr: Sequence[dict], start: int, end: int, min_signals: int = MIN_SIGNALS) -> dict:
    n = len(tr)
    mean = st.mean(x["ret"] for x in tr) if tr else None
    ci = boot_mean(tr) if tr else None
    thirds = thirds_positive(tr, start, end)
    checks = {
        f"сигналов ≥ {min_signals}": n >= min_signals,
        "нижняя граница 97,5 % > 0": ci is not None and ci[0] > 0,
        "средняя ≥ +0,2 % номинала": mean is not None and mean >= MIN_MEAN,
        "плюс в 2 из 3 отрезков": sum(1 for m in thirds if m is not None and m > 0) >= MIN_POSITIVE_THIRDS,
    }
    return {"n": n, "mean": mean, "ci97_5": ci, "thirds": thirds, "checks": checks, "passed": all(checks.values())}


def branch(signals: Sequence[Signal], market: Market, start: int, end: int, min_signals: int = MIN_SIGNALS) -> dict:
    """Ветка по обоим вариантам выхода; проходит, если хотя бы один вариант прошёл все критерии."""
    sig = [s for s in dedup(signals) if entry_minute(s.t_ms) + EXITS_MS["4h"] <= end]
    variants, skipped = {}, {}
    for name, ms in EXITS_MS.items():
        tr, sk = trades(sig, market, ms)
        variants[name] = criteria(tr, start, end, min_signals)
        skipped[name] = sk
        variants[name]["up"] = criteria([x for x in tr if x["side"] > 0], start, end, min_signals)
        variants[name]["down"] = criteria([x for x in tr if x["side"] < 0], start, end, MIN_DOWN_SIGNALS)
    passing = [v for v in variants.values() if v["passed"]]
    return {"signals": len(sig), "variants": variants, "skipped": skipped, "passed": bool(passing),
            "best_lower": max(v["ci97_5"][0] for v in passing) if passing else None}


def paired(i10: Sequence[Signal], i10b: Sequence[Signal], uids: set[str], market: Market, end: int) -> dict:
    """И10б − И10 на заголовках, оценённых обеими ветками; дни — по моменту первой оценки."""
    a = [s for s in dedup(x for x in i10 if x.uid in uids) if entry_minute(s.t_ms) + EXITS_MS["4h"] <= end]
    b = [s for s in dedup(x for x in i10b if x.uid in uids) if entry_minute(s.t_ms) + EXITS_MS["4h"] <= end]
    out = {}
    for name, ms in EXITS_MS.items():
        ta, _ = trades(a, market, ms)
        tb, _ = trades(b, market, ms)
        out[name] = diff_interval(ta, tb)
    out["confirmed"] = any(v["ci97_5"] is not None and v["ci97_5"][0] > 0 for v in out.values() if isinstance(v, dict))
    return out


def diff_interval(ta: Sequence[dict], tb: Sequence[dict], alpha: float = ALPHA, n: int = N_BOOT, seed: int = SEED) -> dict:
    if not ta or not tb:
        return {"n_i10": len(ta), "n_i10b": len(tb), "diff": None, "ci97_5": None}
    by_a: dict[int, list[float]] = {}
    by_b: dict[int, list[float]] = {}
    for x in ta:
        by_a.setdefault(x["first_day"], []).append(x["ret"])
    for x in tb:
        by_b.setdefault(x["first_day"], []).append(x["ret"])
    days = sorted(set(by_a) | set(by_b))
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n):
        pick = rnd.choices(days, k=len(days))
        ra = [r for d in pick for r in by_a.get(d, [])]
        rb = [r for d in pick for r in by_b.get(d, [])]
        if ra and rb:
            diffs.append(st.mean(rb) - st.mean(ra))
    diff = st.mean(x["ret"] for x in tb) - st.mean(x["ret"] for x in ta)
    return {"n_i10": len(ta), "n_i10b": len(tb), "diff": diff, "ci97_5": _quantiles(diffs, alpha) if diffs else None}


# --- проверка -----------------------------------------------------------------------------------------
def check_dates(start: int) -> list[int]:
    """06.12.2026 и далее каждые 4 недели, не дольше 26 недель от старта И10."""
    last = start + MAX_WEEKS * WEEK_MS
    out, t = [], day_ms(FIRST_CHECK)
    while t <= last:
        out.append(t)
        t += CHECK_STEP_DAYS * DAY_MS
    return out


def evaluate(conn: sqlite3.Connection, market: Market, cutoff_ms: int) -> dict:
    start = start_ms(conn)
    if start is None:
        return {"status": "нет оценок замороженной подсказкой"}
    dates = check_dates(start)
    if cutoff_ms not in dates:
        raise ValueError("проверка только в записанные даты: " +
                         ", ".join(f"{datetime.fromtimestamp(d / 1000, UTC):%d.%m.%Y}" for d in dates))
    final = cutoff_ms == dates[-1]
    i10, i10b = load_i10(conn), load_i10b(conn)
    res = {"start": start, "cutoff": cutoff_ms, "final": final, "work": work_counts(conn),
           "i10": branch(i10, market, start, cutoff_ms), "i10b": branch(i10b, market, start, cutoff_ms),
           "paired": paired(i10, i10b, both_scored(conn), market, cutoff_ms)}
    for name in ("i10", "i10b"):
        b = res[name]
        enough = any(v["checks"][f"сигналов ≥ {MIN_SIGNALS}"] for v in b["variants"].values())
        b["status"] = "проходит" if b["passed"] else ("не проходит" if enough or final else "сбор продолжается")
    # Выбор ветки: не прошедшая свои критерии не принимается; прошли обе — выше нижняя граница средней сделки.
    ok = [n for n in ("i10", "i10b") if res[n]["passed"]]
    res["chosen"] = max(ok, key=lambda n: res[n]["best_lower"]) if ok else None
    # Дополнение 14.09: общий набор не прошёл, а down при ≥ 50 сигналах прошёл — новая гипотеза, не вердикт.
    res["down_note"] = (not res["i10"]["passed"]) and any(v["down"]["passed"] for v in res["i10"]["variants"].values())
    return res


# --- сеть ---------------------------------------------------------------------------------------------
class BybitMarket(Market):
    """Контракты (с временем запуска) и минутные свечи Bybit по публичному API, с кэшем в памяти."""

    def __init__(self, get: Callable[[str, dict], dict] | None = None):
        from backtest.xfunding import http_get
        from exchange.bybit_client import MAINNET_REST
        self.get = get or http_get
        self.base = MAINNET_REST + "/v5/market"
        self._launch: dict[str, int] | None = None
        self._minutes: dict[tuple[str, int], dict[int, float]] = {}

    def _instruments(self) -> dict[str, int]:
        if self._launch is None:
            self._launch = {}
            for status in ("Trading", "Closed"):
                cursor = ""
                for _ in range(20):
                    p = {"category": "linear", "limit": 1000, "status": status}
                    if cursor:
                        p["cursor"] = cursor
                    r = self.get(f"{self.base}/instruments-info", p)["result"]
                    for i in r["list"]:
                        if i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual":
                            self._launch[i["symbol"]] = int(i.get("launchTime") or 0)
                    cursor = r.get("nextPageCursor") or ""
                    if not cursor:
                        break
        return self._launch

    def symbol_for(self, ticker: str, t_ms: int) -> str | None:
        inst = self._instruments()
        for sym in (f"{ticker}USDT", f"1000{ticker}USDT"):
            if sym in inst and inst[sym] <= t_ms:
                return sym
        return None

    def minute_open(self, symbol: str, t_ms: int) -> float | None:
        block = t_ms // (1000 * MINUTE_MS)                   # по 1000 минут за запрос
        key = (symbol, block)
        if key not in self._minutes:
            a = block * 1000 * MINUTE_MS
            rows = self.get(f"{self.base}/kline", {"category": "linear", "symbol": symbol, "interval": "1",
                                                   "start": a, "end": a + 1000 * MINUTE_MS - 1, "limit": 1000})["result"]["list"]
            self._minutes[key] = {int(x[0]): float(x[1]) for x in rows}
        return self._minutes[key].get(t_ms)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="И10 / И10б: новости с оценкой ИИ — проверка вперёд")
    ap.add_argument("--db", required=True, help="база бота (market_bot.db)")
    ap.add_argument("--cutoff", default=FIRST_CHECK, help="дата проверки из записанных (06.12.2026, далее +4 недели)")
    ap.add_argument("--out", default="h10.json")
    ap.add_argument("--work-only", action="store_true", help="только работа сборщика — можно смотреть до срока")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    if a.work_only:
        print(json.dumps(work_counts(conn), ensure_ascii=False))
        return 0
    res = evaluate(conn, BybitMarket(), day_ms(a.cutoff))
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    for name in ("i10", "i10b"):
        b = res[name]
        print(f"{name}: {b['status']} — сигналов {b['signals']}")
        for v, c in b["variants"].items():
            print(f"  {v}: n={c['n']} средняя {c['mean']} интервал {c['ci97_5']} отрезки {c['thirds']} → "
                  f"{'проходит' if c['passed'] else 'нет'}")
    print(f"парное И10б − И10: {'подтверждено' if res['paired']['confirmed'] else 'не подтверждено'}; "
          f"выбрана ветка: {res['chosen']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
