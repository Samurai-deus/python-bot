"""
И7 плана трейдера (docs/TRADER_PLAN.md, «Исследование преимущества»): премия за волатильность —
короткий 30-дневный ATM-страддл. Правило и критерии записаны в плане ДО прогона — здесь только
исполнение.

    py -m backtest.vrp_options

Каждый понедельник 08:00 UTC на BTC и ETH продаётся страддл на 30 дней: страйк — open 4h-бара
входа, цена — Блэк–Шоулз при нулевой ставке с волатильностью DVOL (закрытие предыдущего дня)
минус 2 пункта. На экспирации выплата |S_T − K| по open 4h-бара через 30 дней. Издержки —
комиссия 0,03 % и сбор за поставку 0,015 % номинала на каждую из двух ног. Транш — 5 % капитала
номинала на монету. Наблюдение — итог траншей недели в долях капитала; интервал — блочный
бутстреп блоками по 5 недель (транши перекрываются).
"""
import argparse
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import funding_events as fe
from backtest import history, report
from backtest import momentum_xs as mx

DAY_MS, H4_MS, WEEK_MS = mx.DAY_MS, mx.H4_MS, mx.WEEK_MS
HOUR_MS = 3_600_000
COINS = (("BTC", "BTCUSDT"), ("ETH", "ETHUSDT"))
TENOR_D = 30
ENTRY_HOUR = 8
IV_HAIRCUT = 2.0                   # пункты волатильности: продажа по биду
FEE_TRADE = 0.0003                 # на ногу, доля номинала
FEE_DELIVERY = 0.00015             # на ногу, доля номинала
TRANCHE = 0.05                     # номинал транша — доля капитала на монету
BLOCK = 5                          # блок бутстрепа, недель
ALPHA = 0.05
MIN_WEEKS = 150
MIN_MEAN = 0.0015
MAX_DRAWDOWN = 0.15
START = "2021-03-29"
HOLDOUT_DAYS = 75


@dataclass
class Tranche:
    entry_t: int
    exit_t: int
    r: float            # итог траншей недели — доля капитала
    premium: float
    payoff: float
    fees: float
    coins: Tuple[str, ...]


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def atm_straddle(sigma: float, years: float) -> float:
    """Цена ATM-страддла (K = S, ставка 0) в долях цены базового актива."""
    half = sigma * math.sqrt(years) / 2
    return 2 * (2 * norm_cdf(half) - 1)


def iv_at(dvol: Dict[int, float], t: int) -> Optional[float]:
    """Волатильность продажи: закрытие DVOL предыдущего дня минус IV_HAIRCUT, в долях (не процентах)."""
    day0 = t - t % DAY_MS
    v = dvol.get(day0 - DAY_MS)
    return None if v is None else (v - IV_HAIRCUT) / 100


def tranche_result(o4: Dict[int, float], dvol: Dict[int, float], t: int) -> Optional[Tuple[float, float, float]]:
    """(премия, выплата, издержки) в долях номинала для страддла, проданного в t; None без данных."""
    sigma = iv_at(dvol, t)
    s0, st = o4.get(t), o4.get(t + TENOR_D * DAY_MS)
    if sigma is None or sigma <= 0 or not s0 or not st:
        return None
    premium = atm_straddle(sigma, TENOR_D / 365)
    payoff = abs(st / s0 - 1)
    fees = 2 * (FEE_TRADE + FEE_DELIVERY)
    return premium, payoff, fees


def simulate(data: Dict, starts: Sequence[int]) -> List[Tranche]:
    out = []
    for t in starts:
        premium = payoff = fees = 0.0
        coins = []
        for cur, sym in COINS:
            d = data.get(cur)
            res = tranche_result(d["o4"], d["dvol"], t) if d else None
            if res is None:
                continue
            premium += TRANCHE * res[0]
            payoff += TRANCHE * res[1]
            fees += TRANCHE * res[2]
            coins.append(cur)
        if coins:
            out.append(Tranche(t, t + TENOR_D * DAY_MS, premium - payoff - fees, premium, payoff, fees, tuple(coins)))
    return out


def block_ci(rs: Sequence[float], block: int = BLOCK, bootstrap: int = 2000, alpha: float = ALPHA,
             seed: int = 7) -> Tuple[float, float]:
    """Интервал средней — бутстреп блоками подряд идущих наблюдений (зависимость соседних траншей)."""
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


def evaluate(tranches: Sequence[Tranche], bootstrap: int = 2000) -> Dict:
    n = len(tranches)
    if not n:
        return {"weeks": 0, "passed": False, "checks": {}}
    rs = [x.r for x in tranches]
    mean = sum(rs) / n
    low, high = block_ci(rs, bootstrap=bootstrap)
    parts, _ = report.splits(tranches[0].entry_t, tranches[-1].entry_t + 1, holdout_days=0)
    segments = []
    for part in parts:
        chunk = report.within(tranches, part)
        segments.append(sum(x.r for x in chunk) / len(chunk) if chunk else None)
    drawdown = report.max_drawdown(x.r for x in sorted(tranches, key=lambda x: x.exit_t))
    sd = (sum((r - mean) ** 2 for r in rs) / (n - 1)) ** 0.5 if n > 1 else 0.0
    checks = {
        "weeks": n >= MIN_WEEKS,
        "ci": low > 0,
        "mean": mean >= MIN_MEAN,
        "segments": sum(1 for m in segments if m is not None and m > 0) >= 2,
        "drawdown": drawdown <= MAX_DRAWDOWN,
    }
    return {"weeks": n, "mean": mean, "sd": sd, "ci": (low, high), "win_rate": sum(r > 0 for r in rs) / n,
            "segments": segments, "drawdown": drawdown, "checks": checks, "passed": all(checks.values()),
            "premium": sum(x.premium for x in tranches) / n, "payoff": sum(x.payoff for x in tranches) / n,
            "fees": sum(x.fees for x in tranches) / n}


def load(conn, start_ms: int, end_ms: int) -> Dict:
    data = {}
    for cur, sym in COINS:
        bars = history.load_candles(conn, sym, "4h", start_ms - DAY_MS, end_ms + (TENOR_D + 1) * DAY_MS)
        dvol = {int(ts): float(c) for ts, c in conn.execute(
            "SELECT ts, close FROM dvol WHERE currency = ?", (cur,))} if _has_dvol(conn) else {}
        data[cur] = {"o4": {int(b[0]): float(b[1]) for b in bars}, "dvol": dvol}
    return data


def _has_dvol(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'dvol'").fetchone())


def entry_times(start_ms: int, last_exit_ms: int) -> List[int]:
    """Понедельники ENTRY_HOUR:00 UTC, чьи транши закрываются не позже last_exit_ms."""
    return [t + ENTRY_HOUR * HOUR_MS for t in mx.mondays(start_ms, last_exit_ms)
            if t + ENTRY_HOUR * HOUR_MS + TENOR_D * DAY_MS <= last_exit_ms]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="И7: премия за волатильность — короткий страддл")
    parser.add_argument("--db", default=str(history.DEFAULT_DB))
    parser.add_argument("--holdout", action="store_true", help="показать отложенный конец (смотреть один раз)")
    args = parser.parse_args(argv)

    conn = history.connect(args.db)
    _, end_ms = fe.period(conn, 12)
    start_ms = mx.day_ms(START)
    last = end_ms if args.holdout else end_ms - HOLDOUT_DAYS * DAY_MS
    starts = entry_times(start_ms, last)
    data = load(conn, start_ms, last)
    conn.close()

    tranches = simulate(data, starts)
    stats = evaluate(tranches)
    d = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).strftime("%d.%m.%Y")  # noqa: E731
    print(f"И7, транши {d(starts[0])}–{d(starts[-1])} ({'с отложенным концом' if args.holdout else 'без отложенного конца'}), "
          f"страддл {TENOR_D} дней, DVOL − {IV_HAIRCUT:.0f} п., транш {100 * TRANCHE:.0f} % на монету")
    if not stats["weeks"]:
        print("траншей нет")
        return 0
    low, high = stats["ci"]
    seg = " / ".join("—" if m is None else f"{100 * m:+.2f}" for m in stats["segments"])
    failed = [k for k, ok in stats["checks"].items() if not ok]
    print(f"{stats['weeks']} нед., средняя {100 * stats['mean']:+.3f} % капитала [{100 * low:+.3f}; {100 * high:+.3f}] "
          f"(95 %, блоки по {BLOCK}), разброс {100 * stats['sd']:.2f} %, плюсовых {100 * stats['win_rate']:.0f} %, "
          f"отрезки {seg} %, просадка по экспирациям {100 * stats['drawdown']:.1f} %; в неделю: премия "
          f"{100 * stats['premium']:.3f} %, выплата {100 * stats['payoff']:.3f} %, издержки {100 * stats['fees']:.3f} %")
    worst = sorted(tranches, key=lambda x: x.r)[:3]
    print("худшие транши:", "; ".join(f"{d(x.entry_t)} {100 * x.r:+.2f} %" for x in worst))
    print("Вердикт И7:", "принимается" if stats["passed"] else "не принимается: " + ", ".join(failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
