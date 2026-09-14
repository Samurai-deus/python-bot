"""
И17 плана трейдера (docs/TRADER_PLAN.md): правило, подсказанное данными, — краткосрочное
продолжение (И17а, зеркало разворота L = 1 на top30) и сложение слабых плюсов (И17б: недельный
тренд И15, дневной тренд И16, И17а с весами по обратному разбросу). Правило, пороги и решения
по итогу записаны в плане ДО вскрытия отложенного конца — здесь только исполнение.

    py -m backtest.h17 --db data/history_wide.db [--holdout] [--out report.json]

Без --holdout печатается только видимый период (проверка кода: И17а обязана дать ≈ +0,55 % в
неделю — зеркало И15). С --holdout отложенный конец 16.03–14.09.2026 открывается один раз.
"""
import argparse
import json
import math
from datetime import UTC, datetime
from typing import Dict, List, Sequence, Tuple

from backtest import bar_rules as br
from backtest import history
from backtest import momentum_xs as mx
from backtest import wide_search as ws

WEEK_MS = ws.WEEK_MS
CONT = {"family": "continuation", "L": 1, "frac": 0.1, "universe": "top30"}
TREND = {"family": "trend", "L": 30, "universe": "top30"}
MA_RULE, MA_EXIT = "macross_10_50", (24, 2, 2)      # вариант И16, выбранный проверкой вперёд на 2026 год
COMBO_FROM = "2023-01-02"                          # веса И17б — по разбросу с 2023 до отложенного конца
DEMO_MIN_MEAN = 0.003                              # порог демо-ноги: +0,30 % в неделю
MAX_DRAWDOWN = ws.MAX_DRAWDOWN


def weekly_from_trades(trades: Sequence[br.Trade], weeks_t: Sequence[int]) -> Dict[int, float]:
    """Недельный результат на капитал: сумма результатов сделок, вошедших в неделю, × 10 % номинала; пустые недели — 0."""
    out = {t: 0.0 for t in weeks_t[:-1]}
    first = weeks_t[0]
    for tr in trades:
        k = (tr.entry_t - first) // WEEK_MS
        if 0 <= k < len(weeks_t) - 1:
            out[weeks_t[k]] += br.NOTIONAL * tr.r
    return out


def weekly_from_weeks(weeks: Sequence[mx.Week]) -> Dict[int, float]:
    return {w.entry_t: w.r for w in weeks}


def sd(rs: Sequence[float]) -> float:
    n = len(rs)
    if n < 2:
        return 0.0
    m = sum(rs) / n
    return math.sqrt(sum((r - m) ** 2 for r in rs) / (n - 1))


def inverse_sd_weights(legs: Dict[str, Sequence[float]]) -> Dict[str, float]:
    inv = {k: 1 / sd(v) for k, v in legs.items() if sd(v) > 0}
    total = sum(inv.values())
    return {k: v / total for k, v in inv.items()} if total else {}


def combine(legs: Dict[str, Dict[int, float]], weights: Dict[str, float], weeks: Sequence[int]) -> List[Tuple[int, float]]:
    return [(t, sum(weights.get(k, 0.0) * legs[k].get(t, 0.0) for k in weights)) for t in weeks]


def stats(rs: Sequence[float]) -> Dict:
    n = len(rs)
    if not n:
        return {"weeks": 0}
    mean = sum(rs) / n
    s = sd(rs)
    return {"weeks": n, "mean": mean, "sd": s, "se": s / math.sqrt(n) if n else None,
            "sharpe_year": mean / s * math.sqrt(52) if s else None, "drawdown": mx.report.max_drawdown(rs),
            "win_rate": sum(r > 0 for r in rs) / n}


def correlation(a: Sequence[float], b: Sequence[float]) -> float:
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (n - 1)
    return cov / (sd(a) * sd(b)) if sd(a) and sd(b) else 0.0


def legs_series(conn, weeks_all: Sequence[int], start_ms: int, end_ms: int) -> Dict[str, Dict[int, float]]:
    daily = ws.load(conn, start_ms, end_ms)
    legs = {"cont1d": weekly_from_weeks(ws.simulate(daily, CONT, weeks_all)),
            "trend30w": weekly_from_weeks(ws.simulate(daily, TREND, weeks_all))}
    universe = br.weekly_universe(daily, weeks_all)
    bars = br.load_bars(conn, "1d")
    trades: List[br.Trade] = []
    for sym, b in bars.items():
        allowed = br.allowed_bars(b, universe, sym, weeks_all)
        trades += br.trades_for_rule(sym, b, br.Indicators(b), MA_RULE, allowed)[MA_EXIT]
    legs["ma_daily"] = weekly_from_trades(trades, weeks_all)
    return legs


def run(legs: Dict[str, Dict[int, float]], weeks_all: Sequence[int], holdout_start: int, show_holdout: bool) -> Dict:
    weeks = list(weeks_all[:-1])
    visible = [t for t in weeks if t < holdout_start]
    combo_visible = [t for t in visible if t >= mx.day_ms(COMBO_FROM)]
    hold = [t for t in weeks if t >= holdout_start]
    out: Dict = {"holdout_opened": show_holdout, "legs": {}}
    for k, series in legs.items():
        out["legs"][k] = {"visible": stats([series[t] for t in visible]), "combo_window": stats([series[t] for t in combo_visible])}
    weights = inverse_sd_weights({k: [legs[k][t] for t in combo_visible] for k in legs})
    combo_v = [r for _, r in combine(legs, weights, combo_visible)]
    out["combination"] = {"weights": weights, "visible": stats(combo_v),
                          "correlations": {f"{a}/{b}": correlation([legs[a][t] for t in combo_visible], [legs[b][t] for t in combo_visible])
                                           for a in legs for b in legs if a < b}}
    if show_holdout:
        cont_h = [legs["cont1d"][t] for t in hold]
        st = stats(cont_h)
        vis_mean = out["legs"]["cont1d"]["visible"]["mean"]
        st["threshold"] = vis_mean - 2 * st["se"]
        st["not_contradicted"] = st["mean"] >= st["threshold"] and st["drawdown"] <= MAX_DRAWDOWN
        st["demo"] = st["not_contradicted"] and st["mean"] >= DEMO_MIN_MEAN
        out["legs"]["cont1d"]["holdout"] = st
        for k in ("trend30w", "ma_daily"):
            out["legs"][k]["holdout"] = stats([legs[k][t] for t in hold])
        combo_h = [r for _, r in combine(legs, weights, hold)]
        sth = stats(combo_h)
        sth["threshold"] = out["combination"]["visible"]["mean"] - 2 * sth["se"]
        sth["not_contradicted"] = sth["mean"] >= sth["threshold"]
        out["combination"]["holdout"] = sth
    return out


def fmt(st: Dict) -> str:
    if not st.get("weeks"):
        return "недель нет"
    return (f"{st['weeks']} нед., средняя {100 * st['mean']:+.2f} % (SE {100 * st['se']:.2f}), разброс {100 * st['sd']:.2f} %, "
            f"Sharpe {st['sharpe_year']:.2f}/год, просадка {100 * st['drawdown']:.1f} %, плюсовых {100 * st['win_rate']:.0f} %")


def render(out: Dict) -> str:
    lines = ["И17 — видимый период (проверка кода и опорные цифры):"]
    for k, e in out["legs"].items():
        lines.append(f"  {k}: {fmt(e['visible'])}")
    c = out["combination"]
    lines.append(f"  И17б веса (по разбросу с {COMBO_FROM}): " + ", ".join(f"{k}: {100 * v:.0f} %" for k, v in c["weights"].items()))
    lines.append(f"  И17б видимый (с {COMBO_FROM}): {fmt(c['visible'])}")
    lines.append("  корреляции ног: " + ", ".join(f"{k}: {v:+.2f}" for k, v in c["correlations"].items()))
    if out["holdout_opened"]:
        h = out["legs"]["cont1d"]["holdout"]
        lines.append("")
        lines.append("ОТЛОЖЕННЫЙ КОНЕЦ (открыт один раз):")
        lines.append(f"  И17а: {fmt(h)}; порог «не противоречит» {100 * h['threshold']:+.2f} % → "
                     f"{'не противоречит' if h['not_contradicted'] else 'ПРОТИВОРЕЧИЕ'}; порог демо {100 * DEMO_MIN_MEAN:+.2f} % → "
                     f"{'В ДЕМО' if h['demo'] else 'в демо не идёт'}")
        for k in ("trend30w", "ma_daily"):
            lines.append(f"  {k}: {fmt(out['legs'][k]['holdout'])}")
        ch = c["holdout"]
        lines.append(f"  И17б: {fmt(ch)}; порог {100 * ch['threshold']:+.2f} % → {'не противоречит' if ch['not_contradicted'] else 'ПРОТИВОРЕЧИЕ'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И17: продолжение и сложение плюсов на отложенном конце")
    parser.add_argument("--db", default=str(history.DEFAULT_DB.with_name("history_wide.db")))
    parser.add_argument("--holdout", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    conn = history.connect(args.db)
    start_ms = mx.day_ms(ws.START)
    last = conn.execute("SELECT MAX(ts) FROM candles WHERE interval = '4h' AND symbol = 'BTCUSDT'").fetchone()[0]
    end_ms = int(last) + ws.mx.H4_MS
    weeks_all = mx.mondays(start_ms, end_ms)
    holdout_start = weeks_all[-1] - ws.HOLDOUT_WEEKS * WEEK_MS
    legs = legs_series(conn, weeks_all, start_ms, end_ms)
    conn.close()
    out = run(legs, weeks_all, holdout_start, args.holdout)
    print(f"И17: недели {datetime.fromtimestamp(weeks_all[0] / 1000, UTC):%d.%m.%Y}–{datetime.fromtimestamp(weeks_all[-1] / 1000, UTC):%d.%m.%Y}, "
          f"отложенный конец с {datetime.fromtimestamp(holdout_start / 1000, UTC):%d.%m.%Y} ({'ОТКРЫТ' if args.holdout else 'закрыт'})")
    print(render(out))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
