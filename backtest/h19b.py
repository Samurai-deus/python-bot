"""
И19б — межбиржевой фандинг по РАСЧЁТНОЙ ставке на записи вперёд (docs/TRADER_PLAN.md). Записано и заморожено
30.09.2026 — до проверок 19.10 и 16.11 и без просмотра записанных данных (аудит 29.09: кода оценки не было).

    py -m backtest.h19b --db <xfunding_live.db> [--cutoff 2026-11-16] [--out h19b.json]   # вердикт (8 недель)
    py -m backtest.h19b --db <xfunding_live.db> --cutoff 2026-10-19 --lifetime               # только время жизни

Правило — И19 (backtest/xfunding.py: пороги, издержки, портфель, критерии), кроме признака, цен и фандинга;
уточнения, которые план оставлял открытыми (записаны в плане тем же числом, до данных):
• Снимок часа t — строки snap с hour_ms = t, снятые не позже hh:10 (taken_ms − t ≤ 10 мин); более поздние
  в проверку не входят. Биржа без строки в часе t — решения по её ногам в этот час нет: позиция держится,
  вход не рассматривается.
• Ставка в сутки — rate × 24 / interval_h из снимка; без интервала — ставки нет.
• Тот же актив — медиана отношения середин стакана ((bid + ask) / 2) по общим часам, ≥ 24 часов, [0,98; 1,02].
• Цены — стакан из снимка: шорт открывается по bid и закрывается по ask, лонг — по ask и bid; проскальзывание
  и комиссия — издержки на номинал, как в И19. Оценка открытой позиции — по той же стороне, что и закрытие.
• Выплата фандинга — момент next_ms снимка, после которого в следующем снимке той же биржи next_ms больше
  (выплата прошла); ставка — из последнего снимка, снятого до выплаты. Номинал ноги на выплату — по середине
  стакана часа выплаты (нет — по цене входа).
• Окно — от первого часа, где есть снимки всех трёх бирж, до даты проверки (16.11.2026 00:00 UTC, 8 недель).
• 19.10 — только время жизни расхождений: эпизоды |s| ≥ 0,30 % в сутки у ликвидных пар одного актива до первого
  часа с |s| < 0,05 % или сменой знака; незакрытые к концу окна считаются отдельно.
"""
import argparse
import json
import sqlite3
import statistics as st
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from backtest import xfunding as xf

HOUR_MS = xf.HOUR_MS
DAY_MS = xf.DAY_MS
SNAP_WINDOW_MS = 10 * 60_000
CUTOFF = "2026-11-16"
LIFETIME_CHECK = "2026-10-19"


@dataclass(frozen=True)
class Snap:
    rate: float
    next_ms: int | None
    interval_h: float | None
    bid: float
    ask: float
    turnover: float
    taken_ms: int

    @property
    def rate_day(self) -> float | None:
        if not self.interval_h or self.interval_h <= 0:
            return None
        return self.rate * 24 / self.interval_h

    @property
    def mid(self) -> float | None:
        return (self.bid + self.ask) / 2 if self.bid > 0 and self.ask > 0 else None


@dataclass
class Book:
    """Снимки одной биржи по одной монете: час → снимок; выплаты — (время, ставка)."""
    snaps: dict[int, Snap] = field(default_factory=dict)
    payments: list[tuple[int, float]] = field(default_factory=list)


def day_ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1000)


def load(conn: sqlite3.Connection, end_ms: int) -> dict[str, dict[str, Book]]:
    data: dict[str, dict[str, Book]] = {}
    rows = conn.execute("SELECT hour_ms, ex, key, rate, next_ms, interval_h, bid, ask, turnover24h, taken_ms FROM snap "
                        "WHERE hour_ms < ? AND taken_ms - hour_ms <= ? ORDER BY hour_ms", (end_ms, SNAP_WINDOW_MS))
    for hour, ex, key, rate, nxt, interval, bid, ask, turnover, taken in rows:
        data.setdefault(key, {}).setdefault(ex, Book()).snaps[hour] = Snap(
            float(rate or 0.0), int(nxt) if nxt else None, float(interval) if interval else None,
            float(bid or 0.0), float(ask or 0.0), float(turnover or 0.0), int(taken))
    for by_ex in data.values():
        for book in by_ex.values():
            book.payments = payments(book)
    return data


def payments(book: Book) -> list[tuple[int, float]]:
    """Выплата в next_ms снимка, если следующий снимок показывает более позднюю выплату; ставка — последнего
    снимка, снятого до выплаты."""
    out: list[tuple[int, float]] = []
    seq = [book.snaps[h] for h in sorted(book.snaps)]
    last_rate: dict[int, float] = {}
    for s in seq:
        if s.next_ms and s.taken_ms < s.next_ms:
            last_rate[s.next_ms] = s.rate
    seen_next = [s.next_ms for s in seq if s.next_ms]
    for pay, rate in sorted(last_rate.items()):
        if any(n > pay for n in seen_next):
            out.append((pay, rate))
    return out


def same_asset(a: Book, b: Book) -> float | None:
    ratios = [a.snaps[h].mid / b.snaps[h].mid for h in a.snaps
              if h in b.snaps and a.snaps[h].mid and b.snaps[h].mid]
    return st.median(ratios) if len(ratios) >= 24 else None


def pairs_for(data: dict[str, dict[str, Book]]) -> tuple[dict[str, list[tuple[str, str]]], list[str]]:
    ok: dict[str, list[tuple[str, str]]] = {}
    excluded = []
    for k, by_ex in data.items():
        exs = [e for e in xf.EXCHANGES if e in by_ex]
        for i, a in enumerate(exs):
            for b in exs[i + 1:]:
                ratio = same_asset(by_ex[a], by_ex[b])
                if ratio is not None and xf.SAME_ASSET_BAND[0] <= ratio <= xf.SAME_ASSET_BAND[1]:
                    ok.setdefault(k, []).append((a, b))
                elif ratio is not None:
                    excluded.append(f"{k} {a}/{b} {ratio:.3f}")
    return ok, excluded


@dataclass
class Position:
    key: str
    short_ex: str
    long_ex: str
    t0: int
    p_short: float
    p_long: float
    notional: float
    funding: float = 0.0
    costs: float = 0.0


def simulate(data: dict[str, dict[str, Book]], start: int, end: int, p: xf.Params = xf.Params()) -> dict:
    """Прогон по часам [start, end), капитал 1; порядок в часе — как в И19: выплаты → выходы → входы."""
    pairs, excluded = pairs_for(data)
    open_pos: dict[str, Position] = {}
    trades, equity = [], []
    realized = 0.0

    def snap(k: str, ex: str, t: int) -> Snap | None:
        return data[k][ex].snaps.get(t)

    def cost(ex: str, notional: float) -> float:
        return notional * (xf.TAKER_FEE[ex] + p.slippage)

    def spread(k: str, a: str, b: str, t: int) -> float | None:
        sa, sb = snap(k, a, t), snap(k, b, t)
        if sa is None or sb is None or sa.rate_day is None or sb.rate_day is None:
            return None
        return sa.rate_day - sb.rate_day

    def close_prices(pos: Position, t: int) -> tuple[float, float] | None:
        ss, sl = snap(pos.key, pos.short_ex, t), snap(pos.key, pos.long_ex, t)
        if ss is None or sl is None or ss.ask <= 0 or sl.bid <= 0:
            return None
        return ss.ask, sl.bid                       # шорт закрывается покупкой по ask, лонг — продажей по bid

    def value(pos: Position, px: tuple[float, float]) -> float:
        return pos.notional * ((pos.p_short - px[0]) / pos.p_short + (px[1] - pos.p_long) / pos.p_long)

    def liquid(k: str, ex: str, t: int) -> bool:
        s = snap(k, ex, t)
        return s is not None and s.turnover >= p.min_turnover

    for t in range(start, end, HOUR_MS):
        # 1) выплаты за (t − 1 ч, t]: шорт получает ставку × номинал, лонг платит
        for pos in open_pos.values():
            for ex, sign, p0 in ((pos.short_ex, 1, pos.p_short), (pos.long_ex, -1, pos.p_long)):
                for ts, r in data[pos.key][ex].payments:
                    if t - HOUR_MS < ts <= t and ts > pos.t0:
                        s = snap(pos.key, ex, ts - ts % HOUR_MS)
                        px = (s.mid if s is not None else None) or p0
                        pos.funding += sign * r * pos.notional * px / p0
        # 2) выходы
        for k in list(open_pos):
            pos = open_pos[k]
            last = t + HOUR_MS >= end
            s = spread(k, pos.short_ex, pos.long_ex, t)
            if s is None and not last:
                continue                            # снимка нет — решения в этот час нет
            reason = None
            if last:
                reason = "end"
            elif s is not None and s < p.exit:
                reason = "spread"
            elif not (liquid(k, pos.short_ex, t) and liquid(k, pos.long_ex, t)):
                reason = "liquidity"
            elif t - pos.t0 >= p.max_hold:
                reason = "max_hold"
            if reason is None:
                continue
            px = close_prices(pos, t)
            if px is None:
                if not last:
                    continue
                px = (pos.p_short, pos.p_long)       # конец окна без стакана — по цене входа
            pnl = value(pos, px)
            pos.costs += cost(pos.short_ex, pos.notional * px[0] / pos.p_short) + cost(pos.long_ex, pos.notional * px[1] / pos.p_long)
            net = pnl + pos.funding - pos.costs
            realized += net
            trades.append({"key": k, "short": pos.short_ex, "long": pos.long_ex, "t0": pos.t0, "t1": t,
                           "hours": (t - pos.t0) / HOUR_MS, "funding": pos.funding, "price": pnl, "costs": pos.costs,
                           "net": net, "reason": reason, "notional": pos.notional})
            del open_pos[k]
        # 3) входы
        cands = []
        if t + HOUR_MS < end:
            for k, prs in pairs.items():
                if k in open_pos:
                    continue
                best = None
                for a, b in prs:
                    s = spread(k, a, b, t)
                    if s is None or abs(s) < p.entry:
                        continue
                    hi, lo = (a, b) if s > 0 else (b, a)
                    if not (liquid(k, hi, t) and liquid(k, lo, t)):
                        continue
                    sh, sl = snap(k, hi, t), snap(k, lo, t)
                    if sh is None or sl is None or sh.bid <= 0 or sl.ask <= 0:
                        continue
                    if best is None or abs(s) > best[0]:
                        best = (abs(s), hi, lo, sh.bid, sl.ask)
                if best:
                    cands.append(best[:1] + (k,) + best[1:])
        cands.sort(reverse=True)
        for _, k, hi, lo, bid, ask in cands[: max(0, p.max_positions - len(open_pos))]:
            pos = Position(k, hi, lo, t, bid, ask, p.leg)       # шорт продаёт по bid, лонг покупает по ask
            pos.costs = cost(hi, p.leg) + cost(lo, p.leg)
            open_pos[k] = pos
        unreal = 0.0
        for pos in open_pos.values():
            px = close_prices(pos, t)
            unreal += (value(pos, px) if px else 0.0) + pos.funding - pos.costs
        equity.append((t, realized + unreal))
    return {"trades": trades, "equity": equity, "excluded": excluded, "pairs": sum(len(v) for v in pairs.values())}


def window(data: dict[str, dict[str, Book]], cutoff: int) -> tuple[int, int] | None:
    """Первый час со снимками всех трёх бирж — до даты проверки."""
    hours_by_ex: dict[str, set[int]] = {}
    for by_ex in data.values():
        for ex, book in by_ex.items():
            hours_by_ex.setdefault(ex, set()).update(book.snaps)
    if set(hours_by_ex) != set(xf.EXCHANGES):
        return None
    common = set.intersection(*hours_by_ex.values())
    if not common:
        return None
    return min(common), cutoff


def lifetimes(data: dict[str, dict[str, Book]], start: int, end: int, p: xf.Params = xf.Params()) -> dict:
    pairs, _ = pairs_for(data)
    closed, censored = [], 0
    for k, prs in pairs.items():
        for a, b in prs:
            open_at, sign = None, 0
            for t in range(start, end, HOUR_MS):
                sa, sb = data[k][a].snaps.get(t), data[k][b].snaps.get(t)
                if sa is None or sb is None or sa.rate_day is None or sb.rate_day is None:
                    continue
                s = sa.rate_day - sb.rate_day
                if open_at is None:
                    if abs(s) >= p.entry and sa.turnover >= p.min_turnover and sb.turnover >= p.min_turnover:
                        open_at, sign = t, (1 if s > 0 else -1)
                elif sign * s < p.exit:
                    closed.append((t - open_at) / HOUR_MS)
                    open_at = None
            if open_at is not None:
                censored += 1
    q = sorted(closed)
    return {"episodes": len(q), "censored": censored,
            "median_h": st.median(q) if q else None,
            "p75_h": q[int(0.75 * (len(q) - 1))] if q else None,
            "share_ge_4h": sum(1 for x in q if x >= 4) / len(q) if q else None,
            "share_ge_24h": sum(1 for x in q if x >= 24) / len(q) if q else None}


def evaluate(conn: sqlite3.Connection, cutoff_ms: int, lifetime_only: bool = False) -> dict:
    data = load(conn, cutoff_ms)
    w = window(data, cutoff_ms)
    if w is None:
        return {"status": "нет часов со снимками всех трёх бирж"}
    start, end = w
    out = {"start": start, "end": end, "lifetime": lifetimes(data, start, end)}
    if lifetime_only:
        return out
    base = simulate(data, start, end)
    stress = simulate(data, start, end, xf.Params(slippage=0.0010))
    out.update({"pairs": base["pairs"], "excluded": base["excluded"],
                "base": xf.summarize(base), "stress": xf.summarize(stress)})
    out["verdict"] = xf.verdict(out["base"], out["stress"])
    out["reasons"] = {r: sum(1 for x in base["trades"] if x["reason"] == r) for r in ("spread", "liquidity", "max_hold", "end")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="И19б: межбиржевой фандинг по расчётной ставке — проверка на записи")
    ap.add_argument("--db", required=True, help="запись вперёд (xfunding_live.db)")
    ap.add_argument("--cutoff", default=CUTOFF)
    ap.add_argument("--lifetime", action="store_true", help="только время жизни расхождений (проверка 19.10)")
    ap.add_argument("--out", default="h19b.json")
    a = ap.parse_args(argv)
    if not a.lifetime and a.cutoff != CUTOFF:
        raise SystemExit(f"вердикт И19б — только на записанную дату {CUTOFF}")
    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    res = evaluate(conn, day_ms(a.cutoff), a.lifetime)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: res.get(k) for k in ("lifetime", "base", "stress", "verdict")}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
