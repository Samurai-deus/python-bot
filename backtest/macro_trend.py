"""
И8 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): трендследование по многим
рынкам — Nasdaq, золото, серебро, евро, фунт, иена. Правило и критерии записаны в плане ДО прогона.

    py -m backtest.macro_trend              # видимый период 12.1972–12.2012
    py -m backtest.macro_trend --holdout    # отложенный конец 01.2013–… (смотреть один раз)

Раз в месяц по закрытию последнего торгового дня: направление — знак доходности за L месяцев
(L = 12 и 3), вес = 0,04 / годовая волатильность (60 дневных доходностей), не больше 1,0 на рынок;
результат месяца — Σ вес × изменение цены за следующий месяц − 0,15 % оборота (вкл. подгонку).
Данные — таблица macro_daily (backtest/macro_data.py).
"""
import argparse
import math
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import history, report

SERIES = ("NASDAQ", "GOLD", "SILVER", "EURUSD", "GBPUSD", "USDJPY")
LOOKBACKS_M = (12, 3)
VOL_DAYS = 60
TARGET = 0.04
CAP = 1.0
COST = 0.0015
BLOCK = 3
ALPHA = 0.05 / len(LOOKBACKS_M)    # интервал 97,5 %
MIN_MONTHS = 300
MIN_MEAN = 0.004
MAX_DRAWDOWN = 0.25
FIRST = "1972-12"
VISIBLE_LAST = "2012-12"           # последняя ребалансировка видимого периода
HOLDOUT_FIRST = "2013-01"


@dataclass
class Month:
    month: str          # месяц ребалансировки (результат — за следующий)
    r: float
    gross: float
    costs: float
    weights: Dict[str, float]


def month_add(m: str, k: int) -> str:
    y, mo = int(m[:4]), int(m[5:7]) - 1 + k
    return f"{y + mo // 12:04d}-{mo % 12 + 1:02d}"


def month_ends(daily: Sequence[Tuple[str, float]]) -> Dict[str, int]:
    """Месяц → индекс последнего торгового дня месяца в дневном ряду (по возрастанию дат)."""
    out = {}
    for i, (day, _) in enumerate(daily):
        out[day[:7]] = i
    return out


def annual_vol(daily: Sequence[Tuple[str, float]], end_i: int) -> Optional[float]:
    """Годовая волатильность по VOL_DAYS дневным лог-доходностям, заканчивающимся днём end_i."""
    if end_i < VOL_DAYS:
        return None
    rets = [math.log(daily[k][1] / daily[k - 1][1]) for k in range(end_i - VOL_DAYS + 1, end_i + 1)]
    mean = sum(rets) / VOL_DAYS
    var = sum((r - mean) ** 2 for r in rets) / (VOL_DAYS - 1)
    return math.sqrt(var * 252) if var > 0 else None


def targets(data: Dict, month: str, lookback_m: int) -> Dict[str, float]:
    """Веса на месяц после ребалансировки в конце month."""
    out = {}
    nxt, back = month_add(month, 1), month_add(month, -lookback_m)
    for s, d in data.items():
        ends = d["ends"]
        if month not in ends or back not in ends or nxt not in ends:
            continue
        p_now, p_back = d["daily"][ends[month]][1], d["daily"][ends[back]][1]
        vol = annual_vol(d["daily"], ends[month])
        if vol is None or p_now == p_back:
            continue
        sign = 1 if p_now > p_back else -1
        out[s] = sign * min(CAP, TARGET / vol)
    return out


def simulate(data: Dict, months: Sequence[str], lookback_m: int) -> List[Month]:
    out: List[Month] = []
    held: Dict[str, float] = {}
    for m in months:
        w = targets(data, m, lookback_m)
        turnover = sum(abs(w.get(s, 0.0) - held.get(s, 0.0)) for s in set(w) | set(held))
        costs = turnover * COST
        gross = 0.0
        new_held = {}
        for s, x in w.items():
            d = data[s]
            p0, p1 = d["daily"][d["ends"][m]][1], d["daily"][d["ends"][month_add(m, 1)]][1]
            gross += x * (p1 / p0 - 1)
            new_held[s] = x * p1 / p0
        out.append(Month(m, gross - costs, gross, costs, w))
        held = new_held
    return out


def block_ci(rs: Sequence[float], block: int = BLOCK, bootstrap: int = 2000, alpha: float = ALPHA,
             seed: int = 7) -> Tuple[float, float]:
    n = len(rs)
    if n < 2 * block:
        m = sum(rs) / n
        return m, m
    starts = list(range(n - block + 1))
    rng = random.Random(seed)
    means = []
    for _ in range(bootstrap):
        sample = []
        while len(sample) < n:
            s = rng.choice(starts)
            sample.extend(rs[s:s + block])
        means.append(sum(sample[:n]) / n)
    means.sort()
    return means[int(alpha / 2 * bootstrap)], means[int((1 - alpha / 2) * bootstrap) - 1]


def evaluate(months: Sequence[Month], bootstrap: int = 2000) -> Dict:
    n = len(months)
    if not n:
        return {"months": 0, "passed": False, "checks": {}}
    rs = [x.r for x in months]
    mean = sum(rs) / n
    low, high = block_ci(rs, bootstrap=bootstrap)
    third = n // 3
    segments = [sum(rs[i * third:(i + 1) * third if i < 2 else n]) / len(rs[i * third:(i + 1) * third if i < 2 else n])
                for i in range(3)] if n >= 3 else []
    drawdown = report.max_drawdown(rs)
    sd = (sum((r - mean) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    checks = {
        "months": n >= MIN_MONTHS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"months": n, "mean": mean, "sd": sd, "ci": (low, high), "win_rate": sum(r > 0 for r in rs) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values()),
            "costs": sum(x.costs for x in months) / n, "gross": sum(x.gross for x in months) / n}


def holdout_verdict(months: Sequence[Month]) -> Tuple[bool, float, float]:
    """Критерий отложенного конца из плана: средний месяц выше 0 и просадка не больше 25 %."""
    rs = [x.r for x in months]
    mean = sum(rs) / len(rs)
    dd = report.max_drawdown(rs)
    return mean > 0 and dd <= MAX_DRAWDOWN, mean, dd


def load(conn) -> Dict:
    data = {}
    for s in SERIES:
        daily = [(d, float(v)) for d, v in conn.execute(
            "SELECT day, value FROM macro_daily WHERE series = ? ORDER BY day", (s,))]
        if daily:
            data[s] = {"daily": daily, "ends": month_ends(daily)}
    return data


def months_between(first: str, last: str) -> List[str]:
    out, m = [], first
    while m <= last:
        out.append(m)
        m = month_add(m, 1)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И8: трендследование по многим рынкам")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--holdout", action="store_true", help="отложенный конец 01.2013–… (смотреть один раз)")
    args = parser.parse_args(argv)
    conn = history.connect(args.db)
    data = load(conn)
    conn.close()
    # последний общий месяц может быть неполным (FRED-курсы запаздывают на дни) — последняя
    # ребалансировка отложенного конца на два месяца раньше, чтобы месяц результата был полным
    last_full = min(max(d["ends"]) for d in data.values())
    hidden_last = month_add(last_full, -2)
    for lookback in LOOKBACKS_M:
        visible = simulate(data, months_between(FIRST, VISIBLE_LAST), lookback)
        stats = evaluate(visible)
        low, high = stats["ci"]
        seg = " / ".join(f"{100 * x:+.2f}" for x in stats["segments"])
        failed = [k for k, ok in stats["checks"].items() if not ok]
        print(f"L = {lookback:>2} мес, видимый {FIRST}–{VISIBLE_LAST}: {stats['months']} мес., средний "
              f"{100 * stats['mean']:+.3f} % [{100 * low:+.3f}; {100 * high:+.3f}] (97,5 %, блоки по {BLOCK}), "
              f"разброс {100 * stats['sd']:.2f} %, плюсовых {100 * stats['win_rate']:.0f} %, отрезки {seg} %, "
              f"просадка {100 * stats['drawdown']:.1f} %; до издержек {100 * stats['gross']:+.3f} %, "
              f"издержки {100 * stats['costs']:.3f} % → {'ПРОХОДИТ' if stats['passed'] else 'не проходит: ' + ', '.join(failed)}")
        if args.holdout:
            hidden = simulate(data, months_between(HOLDOUT_FIRST, hidden_last), lookback)
            ok, mean, dd = holdout_verdict(hidden)
            print(f"   отложенный конец {HOLDOUT_FIRST}–{hidden_last}: {len(hidden)} мес., средний "
                  f"{100 * mean:+.3f} %, просадка {100 * dd:.1f} % → {'противоречия нет' if ok else 'ПРОТИВОРЕЧИЕ'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
