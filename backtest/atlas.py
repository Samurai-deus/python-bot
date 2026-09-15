"""
Атлас закономерностей (docs/MARKET_ATLAS.md): разведочный разбор всех собранных данных — 393
бессрочных крипто-контракта Bybit с 2021 года (4h, фандинг), 1h по 40 самым ликвидным, открытый
интерес по 27 монетам бота за год, календарь ФРС и CPI. Это ПОИСК ЗАКОНОМЕРНОСТЕЙ, а не проверка
гипотез: каждая цифра здесь подсмотрена на всех данных, включая уже открытый отложенный конец,
поэтому ничего из атласа не принимается — только записывается как гипотеза для наблюдения вперёд.

    py -m backtest.atlas --db data/history_wide.db --bot-db data/history.db --out docs/MARKET_ATLAS.md
"""
import argparse
import bisect
import json
import math
import random
from collections import defaultdict
from datetime import UTC, datetime
from typing import Dict, List, Optional, Sequence, Tuple

from backtest import history
from backtest import momentum_xs as mx
from backtest import wide_search as ws

DAY_MS, WEEK_MS, H4_MS, HOUR_MS = ws.DAY_MS, ws.WEEK_MS, mx.H4_MS, mx.HOUR_MS
START = "2021-01-04"
BOOT = 1000
SEED = 7
FOMC = ["2021-01-27", "2021-03-17", "2021-04-28", "2021-06-16", "2021-07-28", "2021-09-22", "2021-11-03", "2021-12-15",
        "2022-01-26", "2022-03-16", "2022-05-04", "2022-06-15", "2022-07-27", "2022-09-21", "2022-11-02", "2022-12-14",
        "2023-02-01", "2023-03-22", "2023-05-03", "2023-06-14", "2023-07-26", "2023-09-20", "2023-11-01", "2023-12-13",
        "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
        "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
        "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29"]
CPI: List[str] = []   # заполняется из аргумента --cpi (файл со списком дат), если есть


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------

def mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def sd(xs: Sequence[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def boot_ci(groups: Sequence[Sequence[float]], boot: int = BOOT, alpha: float = 0.05, seed: int = SEED) -> Tuple[float, float]:
    """Блочный бутстреп: группа (день/неделя) — единица выборки; интервал средней по наблюдениям."""
    groups = [g for g in groups if g]
    if len(groups) < 2:
        return float("nan"), float("nan")
    rng = random.Random(seed)
    means = []
    n = len(groups)
    for _ in range(boot):
        s = c = 0.0
        for _ in range(n):
            g = groups[rng.randrange(n)]
            s += sum(g)
            c += len(g)
        means.append(s / c)
    means.sort()
    return means[int(alpha / 2 * boot)], means[int((1 - alpha / 2) * boot) - 1]


def by_day(obs: Sequence[Tuple[int, float]]) -> List[List[float]]:
    d: Dict[int, List[float]] = defaultdict(list)
    for day, v in obs:
        d[day].append(v)
    return list(d.values())


def summarize(obs: Sequence[Tuple[int, float]], scale: float = 100.0) -> Dict:
    """obs — (день, значение). Средняя, интервал 95 % по дням, число наблюдений и дней."""
    vals = [v for _, v in obs]
    lo, hi = boot_ci(by_day(obs))
    return {"n": len(vals), "days": len({d for d, _ in obs}), "mean": scale * mean(vals), "lo": scale * lo, "hi": scale * hi,
            "sd": scale * sd(vals)}


def fmt(s: Dict, unit: str = "%") -> str:
    if not s["n"]:
        return "нет данных"
    return f"{s['mean']:+.3f} {unit} [{s['lo']:+.3f}; {s['hi']:+.3f}], n = {s['n']}, дней {s['days']}"


def terciles(values: Dict[int, float]) -> Dict[int, int]:
    """день → 0/1/2 по терцилям величины (по всем дням)."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    return {d: min(2, 3 * i // n) for i, (d, _) in enumerate(items)}


def day_ms(day: str) -> int:
    return mx.day_ms(day)


def day_index(day: str) -> int:
    return day_ms(day) // DAY_MS


# ---------------------------------------------------------------------------
# Данные: дневные ряды
# ---------------------------------------------------------------------------

class Atlas:
    def __init__(self, data: Dict[str, ws.Series], start_ms: int, end_ms: int):
        self.data = data
        self.d0, self.d1 = start_ms // DAY_MS, end_ms // DAY_MS
        self.days = list(range(self.d0, self.d1))
        # дневные доходности
        self.ret: Dict[str, Dict[int, float]] = {}
        for s, ser in data.items():
            r = {}
            for d in self.days:
                a, b = ser.close.get(d - 1), ser.close.get(d)
                if a and b:
                    r[d] = b / a - 1
            self.ret[s] = r
        # вселенная по дням (правило И15) и top30 по обороту
        self.universe: Dict[int, List[str]] = {}
        for d in self.days:
            scored = []
            for s, ser in data.items():
                if ser.first_day is None or d - ser.first_day + 1 < ws.MIN_HISTORY_D or d not in self.ret[s]:
                    continue
                turn = ws.mean_turnover(ser, d - 1)
                if turn is None or turn < ws.MIN_TURNOVER:
                    continue
                scored.append((turn, s))
            scored.sort(reverse=True)
            self.universe[d] = [s for _, s in scored]
        self.btc = self.ret.get("BTCUSDT", {})

    def top(self, d: int, n: int = 30) -> List[str]:
        return self.universe.get(d, [])[:n]

    def ew_return(self, d: int, syms: Sequence[str]) -> Optional[float]:
        rs = [self.ret[s][d] for s in syms if d in self.ret[s]]
        return mean(rs) if rs else None

    # --- регимы -------------------------------------------------------------
    def regime_vars(self) -> Dict[str, Dict[int, float]]:
        """По дням: волатильность BTC 30 дн (годовая), средняя корреляция top30 с BTC за 30 дн, дисперсия
        (SD дневных доходностей top30), BTC выше/ниже 200-дневной средней."""
        out = {"btc_vol": {}, "corr": {}, "dispersion": {}, "btc_above_ma200": {}}
        closes = self.data["BTCUSDT"].close
        for d in self.days:
            win = [self.btc.get(d - k) for k in range(30)]
            if all(v is not None for v in win):
                out["btc_vol"][d] = sd(win) * math.sqrt(365)
            syms = self.top(d)
            if syms:
                rs = [self.ret[s][d] for s in syms if d in self.ret[s]]
                if len(rs) >= 10:
                    out["dispersion"][d] = sd(rs)
                cors = []
                for s in syms[:30]:
                    xs = [(self.ret[s].get(d - k), self.btc.get(d - k)) for k in range(30)]
                    xs = [(a, b) for a, b in xs if a is not None and b is not None]
                    if len(xs) >= 25:
                        a, b = [x for x, _ in xs], [y for _, y in xs]
                        if sd(a) and sd(b):
                            ma, mb = mean(a), mean(b)
                            cors.append(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / ((len(a) - 1) * sd(a) * sd(b)))
                if cors:
                    out["corr"][d] = mean(cors)
            ma = [closes.get(d - k) for k in range(200)]
            if all(ma) and closes.get(d):
                out["btc_above_ma200"][d] = 1.0 if closes[d] > mean(ma) else 0.0
        return out

    def weekly_strategy(self, cfg: dict, weeks: Sequence[int]) -> Dict[int, float]:
        return {w.entry_t: w.r for w in ws.simulate(self.data, cfg, weeks)}


# ---------------------------------------------------------------------------
# Разделы
# ---------------------------------------------------------------------------

def section_regimes(atlas: Atlas, weeks: Sequence[int]) -> Tuple[str, Dict]:
    vars_ = atlas.regime_vars()
    trend = atlas.weekly_strategy({"family": "trend", "L": 30, "universe": "top30"}, weeks)
    cont = atlas.weekly_strategy({"family": "continuation", "L": 1, "frac": 0.1, "universe": "top30"}, weeks)
    mom = atlas.weekly_strategy({"family": "momentum", "L": 28, "frac": 0.1, "universe": "top30"}, weeks)
    lines = ["## 1. Режимы рынка: когда работает тренд, а когда продолжение", "",
             "Недельный результат правил (валовая 1) по терцилям состояния рынка на понедельник (состояние — по данным до понедельника).", ""]
    res = {}
    for name, series in (("тренд L = 30 top30", trend), ("продолжение 1 дн top30", cont), ("моментум 28 дн top30", mom)):
        lines.append(f"**{name}**")
        lines.append("")
        lines.append("| Переменная | нижняя треть | средняя | верхняя треть |")
        lines.append("|---|---|---|---|")
        for var, label in (("btc_vol", "волатильность BTC 30 дн"), ("corr", "корреляция top30 с BTC"), ("dispersion", "дисперсия top30")):
            terc = terciles({d: v for d, v in vars_[var].items()})
            cells = []
            for k in range(3):
                obs = [(t // DAY_MS, r) for t, r in series.items() if terc.get(t // DAY_MS) == k]
                s = summarize(obs)
                cells.append(f"{s['mean']:+.2f} [{s['lo']:+.2f}; {s['hi']:+.2f}] (n={s['n']})" if s["n"] else "—")
                res[f"{name}|{var}|{k}"] = s
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
        above = {d: v for d, v in vars_["btc_above_ma200"].items()}
        cells = []
        for k, lab in ((0.0, "BTC ниже MA200"), (1.0, "BTC выше MA200")):
            obs = [(t // DAY_MS, r) for t, r in series.items() if above.get(t // DAY_MS) == k]
            s = summarize(obs)
            cells.append(f"{lab}: {s['mean']:+.2f} [{s['lo']:+.2f}; {s['hi']:+.2f}] (n={s['n']})" if s["n"] else f"{lab}: —")
            res[f"{name}|ma200|{k}"] = s
        lines.append(f"| режим BTC | {cells[0]} | | {cells[1]} |")
        lines.append("")
    # средняя корреляция и дисперсия по годам — описание
    lines.append("**Состояние рынка по годам** (медианы по дням):")
    lines.append("")
    lines.append("| Год | волатильность BTC | корреляция top30 с BTC | дисперсия top30 (SD дневных, %) | контрактов во вселенной |")
    lines.append("|---|---|---|---|---|")
    for year in range(2021, 2027):
        d_lo, d_hi = day_index(f"{year}-01-01"), day_index(f"{year + 1}-01-01")
        def med(var):
            xs = sorted(v for d, v in vars_[var].items() if d_lo <= d < d_hi)
            return xs[len(xs) // 2] if xs else float("nan")
        uni = sorted(len(atlas.universe.get(d, [])) for d in range(d_lo, min(d_hi, atlas.d1)))
        lines.append(f"| {year} | {100 * med('btc_vol'):.0f} % | {med('corr'):.2f} | {100 * med('dispersion'):.1f} | {uni[len(uni) // 2] if uni else 0} |")
    lines.append("")
    return "\n".join(lines), res


def section_calendar(atlas: Atlas, bars_1h: Dict[str, ws.Series], hourly: Dict[str, dict]) -> Tuple[str, Dict]:
    lines = ["## 2. Календарь: день недели, час, выходные, выплата фандинга", ""]
    res = {}
    names = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    lines.append("**Дневная доходность по дням недели** (UTC; BTC и равновзвешенный top30):")
    lines.append("")
    lines.append("| День | BTC | top30 | доля дней в плюсе (top30) |")
    lines.append("|---|---|---|---|")
    for wd in range(7):
        days = [d for d in atlas.days if (d + 3) % 7 == wd]   # день 0 эпохи — четверг
        b = summarize([(d, atlas.btc[d]) for d in days if d in atlas.btc])
        t_obs = []
        for d in days:
            r = atlas.ew_return(d, atlas.top(d))
            if r is not None:
                t_obs.append((d, r))
        t = summarize(t_obs)
        pos = mean([1.0 if r > 0 else 0.0 for _, r in t_obs]) if t_obs else float("nan")
        res[f"dow|{wd}"] = {"btc": b, "top30": t}
        lines.append(f"| {names[wd]} | {b['mean']:+.3f} [{b['lo']:+.3f}; {b['hi']:+.3f}] | {t['mean']:+.3f} [{t['lo']:+.3f}; {t['hi']:+.3f}] | {100 * pos:.0f} % |")
    lines.append("")
    # выходные: волатильность и оборот
    wk_vol = [abs(atlas.btc[d]) for d in atlas.days if d in atlas.btc and (d + 3) % 7 < 5]
    we_vol = [abs(atlas.btc[d]) for d in atlas.days if d in atlas.btc and (d + 3) % 7 >= 5]
    turn_wk = [atlas.data["BTCUSDT"].turnover.get(d, 0) for d in atlas.days if (d + 3) % 7 < 5]
    turn_we = [atlas.data["BTCUSDT"].turnover.get(d, 0) for d in atlas.days if (d + 3) % 7 >= 5]
    lines.append(f"Выходные против будней (BTC): средний |дневной ход| {100 * mean(we_vol):.2f} % против {100 * mean(wk_vol):.2f} %; "
                 f"оборот {mean(turn_we) / 1e9:.2f} против {mean(turn_wk) / 1e9:.2f} млрд USDT в день.")
    lines.append("")
    # час дня по 1h (40 контрактов): средняя доходность и доля оборота
    if hourly:
        lines.append("**Час суток (UTC), 1h-бары 40 самых ликвидных контрактов, равновзвешенно:** средняя доходность часа и доля суточного оборота.")
        lines.append("")
        lines.append("| Час | доходность, б.п. [95 %] | доля оборота |")
        lines.append("|---|---|---|")
        for h in range(24):
            s = summarize(hourly["ret"][h], scale=10_000)
            share = hourly["turn"][h] / sum(hourly["turn"].values()) if sum(hourly["turn"].values()) else 0
            res[f"hour|{h}"] = {"ret": s, "turn_share": share}
            mark = " ←фандинг" if h in (0, 8, 16) else ""
            lines.append(f"| {h:02d}{mark} | {s['mean']:+.2f} [{s['lo']:+.2f}; {s['hi']:+.2f}] | {100 * share:.1f} % |")
        lines.append("")
        lines.append("Единица — базисный пункт (0,01 %). Издержки круга ≈ 21 б.п.: любой часовой эффект меньше этого вживую не собрать.")
        lines.append("")
    return "\n".join(lines), res


def hourly_stats(bars_1h: Dict[str, ws.Series], d0: int, d1: int) -> Dict[str, dict]:
    """1h: доходность по часам (open→open следующего часа) и оборот по часам, по всем контрактам."""
    ret: Dict[int, List[Tuple[int, float]]] = {h: [] for h in range(24)}
    turn: Dict[int, float] = {h: 0.0 for h in range(24)}
    for s, ser in bars_1h.items():
        opens = ser.open
        ts_sorted = sorted(t for t in opens if d0 * DAY_MS <= t < d1 * DAY_MS)
        for t in ts_sorted:
            nxt = opens.get(t + HOUR_MS)
            if nxt and opens[t]:
                h = (t // HOUR_MS) % 24
                ret[h].append((t // DAY_MS, nxt / opens[t] - 1))
                turn[h] += ser.turnover.get(t, 0.0)
    return {"ret": ret, "turn": turn}


def section_funding(atlas: Atlas) -> Tuple[str, Dict]:
    """Децили ставки фандинга (средняя за 3 дня, к 8 ч) → доходность следующих 1 и 7 дней; вселенная."""
    lines = ["## 3. Фандинг как мера толпы: децили ставки → что дальше", ""]
    res = {}
    obs1: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    obs7: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    fund_rows = []
    for d in atlas.days[::1]:
        syms = atlas.universe.get(d, [])
        t = d * DAY_MS
        vals = []
        for s in syms:
            ser = atlas.data[s]
            lo, hi = bisect.bisect_left(ser.fund_ts, t - 3 * DAY_MS), bisect.bisect_left(ser.fund_ts, t)
            if hi - lo >= 3:
                vals.append((mean(ser.fund_rate[lo:hi]), s))
        if len(vals) < 20:
            continue
        vals.sort()
        n = len(vals)
        for i, (f, s) in enumerate(vals):
            dec = min(9, 10 * i // n)
            r1 = atlas.ret[s].get(d)
            if r1 is not None:
                obs1[dec].append((d, r1))
            c = atlas.data[s].close
            if c.get(d - 1) and c.get(d + 6):
                obs7[dec].append((d, c[d + 6] / c[d - 1] - 1))
            fund_rows.append(f)
    lines.append("Ставка — средняя за 3 дня до дня t по всей вселенной; дециль 0 — самые отрицательные (шортам платят), 9 — самые положительные (лонги платят). Доходность — с открытия дня t.")
    lines.append("")
    lines.append("| Дециль фандинга | следующий день, % | следующие 7 дней, % |")
    lines.append("|---|---|---|")
    for dec in range(10):
        s1, s7 = summarize(obs1[dec]), summarize(obs7[dec])
        res[f"funding|{dec}"] = {"d1": s1, "d7": s7}
        lines.append(f"| {dec} | {s1['mean']:+.3f} [{s1['lo']:+.3f}; {s1['hi']:+.3f}] (n={s1['n']}) | {s7['mean']:+.2f} [{s7['lo']:+.2f}; {s7['hi']:+.2f}] (n={s7['n']}) |")
    lines.append("")
    fund_rows.sort()
    if fund_rows:
        lines.append(f"Распределение ставки (за 8 ч): медиана {1e4 * fund_rows[len(fund_rows) // 2]:.2f} б.п., 10-й процентиль {1e4 * fund_rows[len(fund_rows) // 10]:.2f}, "
                     f"90-й {1e4 * fund_rows[9 * len(fund_rows) // 10]:.2f}; доля отрицательных {100 * mean([1.0 if f < 0 else 0.0 for f in fund_rows]):.0f} %.")
    lines.append("")
    return "\n".join(lines), res


def section_age(atlas: Atlas) -> Tuple[str, Dict]:
    lines = ["## 4. Возраст листинга: как ведут себя новые контракты", ""]
    res = {}
    buckets = [(0, 7, "дни 1–7"), (7, 30, "дни 8–30"), (30, 90, "дни 31–90"), (90, 180, "дни 91–180"), (180, 365, "дни 181–365"), (365, 10_000, "старше года")]
    lines.append("Дневная доходность контракта минус доходность BTC в тот же день (альфа к рынку), по возрасту с первой свечи. Без фильтра по обороту — все 393 контракта.")
    lines.append("")
    lines.append("| Возраст | альфа к BTC в день, % | доля дней в плюсе | n |")
    lines.append("|---|---|---|---|")
    for lo, hi, label in buckets:
        obs = []
        for s, ser in atlas.data.items():
            if ser.first_day is None:
                continue
            for d, r in atlas.ret[s].items():
                age = d - ser.first_day
                if lo <= age < hi and d in atlas.btc:
                    obs.append((d, r - atlas.btc[d]))
        st = summarize(obs)
        pos = mean([1.0 if v > 0 else 0.0 for _, v in obs]) if obs else float("nan")
        res[f"age|{label}"] = st
        lines.append(f"| {label} | {st['mean']:+.3f} [{st['lo']:+.3f}; {st['hi']:+.3f}] | {100 * pos:.0f} % | {st['n']} |")
    lines.append("")
    lines.append("Оговорка: выжившие — контракты, снятые с торгов, в данных нет; для молодых контрактов это завышает результат.")
    lines.append("")
    return "\n".join(lines), res


def section_leadlag(atlas: Atlas) -> Tuple[str, Dict]:
    lines = ["## 5. BTC ведёт? Запаздывание альткоинов и бета", ""]
    res = {}
    # корреляция дневной доходности альта с доходностью BTC вчера / сегодня по уровням ликвидности
    tiers = [(0, 10, "top 1–10"), (10, 30, "11–30"), (30, 100, "31–100"), (100, 1000, "101+")]
    lines.append("Корреляция дневной доходности контракта с доходностью BTC в тот же день и в предыдущий день, по уровням ликвидности (место по обороту за 30 дней).")
    lines.append("")
    lines.append("| Уровень | corr(alt_t, BTC_t) | corr(alt_t, BTC_{t−1}) | средняя бета к BTC |")
    lines.append("|---|---|---|---|")
    for lo, hi, label in tiers:
        same, lag, betas = [], [], []
        for d in atlas.days:
            syms = atlas.universe.get(d, [])[lo:hi]
            b0, b1 = atlas.btc.get(d), atlas.btc.get(d - 1)
            if b0 is None or b1 is None:
                continue
            for s in syms:
                if s == "BTCUSDT" or d not in atlas.ret[s]:
                    continue
                same.append((atlas.ret[s][d], b0))
                lag.append((atlas.ret[s][d], b1))
        def corr(pairs):
            a, b = [x for x, _ in pairs], [y for _, y in pairs]
            if len(a) < 10 or not sd(a) or not sd(b):
                return float("nan")
            ma, mb = mean(a), mean(b)
            return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / ((len(a) - 1) * sd(a) * sd(b))
        c0, c1 = corr(same), corr(lag)
        a, b = [x for x, _ in same], [y for _, y in same]
        beta = c0 * sd(a) / sd(b) if len(a) > 10 and sd(b) else float("nan")
        res[f"leadlag|{label}"] = {"same": c0, "lag": c1, "beta": beta, "n": len(same)}
        lines.append(f"| {label} | {c0:.2f} | {c1:+.3f} | {beta:.2f} |")
    lines.append("")
    # доходность корзин по бете (лонг низкая бета / шорт высокая) — недельная, top30..100
    lines.append("Корзины по бете к BTC (60 дней), пересчёт по понедельникам, вселенная top100: равновзвешенная недельная доходность корзины.")
    lines.append("")
    weeks = mx.mondays(atlas.d0 * DAY_MS, atlas.d1 * DAY_MS)
    q_obs: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    for t in weeks:
        d = t // DAY_MS
        syms = [s for s in atlas.universe.get(d, [])[:100] if s != "BTCUSDT"]
        betas = []
        for s in syms:
            pairs = [(atlas.ret[s].get(d - k), atlas.btc.get(d - k)) for k in range(1, 61)]
            pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
            if len(pairs) < 40:
                continue
            a, b = [x for x, _ in pairs], [y for _, y in pairs]
            if sd(b):
                ma, mb = mean(a), mean(b)
                betas.append((sum((x - ma) * (y - mb) for x, y in zip(a, b)) / ((len(a) - 1) * sd(b) ** 2), s))
        if len(betas) < 20:
            continue
        betas.sort()
        n = len(betas)
        for i, (beta, s) in enumerate(betas):
            q = min(3, 4 * i // n)
            c = atlas.data[s].close
            if c.get(d - 1) and c.get(d + 6):
                q_obs[q].append((d, c[d + 6] / c[d - 1] - 1))
    lines.append("| Квартиль беты | неделя, % |")
    lines.append("|---|---|")
    for q, label in enumerate(("низкая бета", "2", "3", "высокая бета")):
        s = summarize(q_obs[q])
        res[f"beta|{q}"] = s
        lines.append(f"| {label} | {s['mean']:+.2f} [{s['lo']:+.2f}; {s['hi']:+.2f}] (n={s['n']}) |")
    lines.append("")
    return "\n".join(lines), res


def section_tails(atlas: Atlas) -> Tuple[str, Dict]:
    lines = ["## 6. Хвосты и волатильность: чего ждать от риска 40 %", ""]
    res = {}
    weeks = mx.mondays(atlas.d0 * DAY_MS, atlas.d1 * DAY_MS)
    tiers = [(0, 10, "top 1–10"), (10, 30, "11–30"), (30, 100, "31–100")]
    lines.append("Недельные доходности контрактов по уровням ликвидности: разброс, доля недель с |ходом| > 20 %, худшая неделя (медиана по контрактам).")
    lines.append("")
    lines.append("| Уровень | SD недели | доля недель |ход| > 20 % | > 40 % | медианная худшая неделя контракта |")
    lines.append("|---|---|---|---|---|")
    for lo, hi, label in tiers:
        rs, worst = [], defaultdict(lambda: 0.0)
        for t in weeks:
            d = t // DAY_MS
            for s in atlas.universe.get(d, [])[lo:hi]:
                c = atlas.data[s].close
                if c.get(d - 1) and c.get(d + 6):
                    r = c[d + 6] / c[d - 1] - 1
                    rs.append(r)
                    worst[s] = min(worst[s], r)
        w = sorted(worst.values())
        res[f"tails|{label}"] = {"sd": sd(rs), "p20": mean([1.0 if abs(r) > 0.2 else 0.0 for r in rs]), "p40": mean([1.0 if abs(r) > 0.4 else 0.0 for r in rs]),
                                 "worst_med": w[len(w) // 2] if w else float("nan"), "n": len(rs)}
        r_ = res[f"tails|{label}"]
        lines.append(f"| {label} | {100 * r_['sd']:.1f} % | {100 * r_['p20']:.1f} % | {100 * r_['p40']:.1f} % | {100 * r_['worst_med']:.0f} % |")
    lines.append("")
    # вол-таргетинг BTC: buy&hold vs позиция = 0.5 / vol30
    btc_days = [d for d in atlas.days if d in atlas.btc]
    bh = [atlas.btc[d] for d in btc_days]
    vt = []
    for d in btc_days:
        win = [atlas.btc.get(d - k) for k in range(1, 31)]
        if all(v is not None for v in win) and sd(win) > 0:
            vt.append(atlas.btc[d] * min(3.0, 0.5 / (sd(win) * math.sqrt(365))))
    def sharpe(xs):
        return mean(xs) / sd(xs) * math.sqrt(365) if sd(xs) else float("nan")
    res["voltarget"] = {"bh_sharpe": sharpe(bh), "vt_sharpe": sharpe(vt), "bh_dd": mx.report.max_drawdown(bh), "vt_dd": mx.report.max_drawdown(vt)}
    lines.append(f"BTC 2021–2026: купить-и-держать — Sharpe {res['voltarget']['bh_sharpe']:.2f}, просадка {100 * res['voltarget']['bh_dd']:.0f} % (сумма дневных); "
                 f"та же позиция с размером под 50 % годовых по волатильности 30 дн — Sharpe {res['voltarget']['vt_sharpe']:.2f}, просадка {100 * res['voltarget']['vt_dd']:.0f} %.")
    lines.append("")
    return "\n".join(lines), res


def section_macro(atlas: Atlas, cpi: Sequence[str]) -> Tuple[str, Dict]:
    lines = ["## 7. Макро-календарь: дни ФРС и CPI", ""]
    res = {}
    def event_stats(dates: Sequence[str], label: str):
        idx = {day_index(x) for x in dates}
        rows = []
        for offset, name in ((-1, "день до"), (0, "день события"), (1, "день после")):
            obs = [(d, atlas.btc[d]) for d in atlas.days if (d - offset) in idx and d in atlas.btc]
            absobs = [(d, abs(v)) for d, v in obs]
            s, a = summarize(obs), summarize(absobs)
            rows.append((name, s, a))
        others = [(d, abs(atlas.btc[d])) for d in atlas.days if d in atlas.btc and not any((d - o) in idx for o in (-1, 0, 1))]
        base = summarize(others)
        res[label] = {"rows": [(n, s, a) for n, s, a in rows], "base_abs": base}
        lines.append(f"**{label}** ({len(idx & set(atlas.days))} событий в периоде). BTC: средняя доходность и средний |ход| дня; обычный день |ход| {base['mean']:.2f} %.")
        lines.append("")
        lines.append("| | доходность, % | |ход|, % |")
        lines.append("|---|---|---|")
        for name, s, a in rows:
            lines.append(f"| {name} | {s['mean']:+.2f} [{s['lo']:+.2f}; {s['hi']:+.2f}] | {a['mean']:.2f} [{a['lo']:.2f}; {a['hi']:.2f}] |")
        lines.append("")
    event_stats(FOMC, "Решения ФРС (FOMC)")
    if cpi:
        event_stats(cpi, "Публикации CPI США")
    else:
        lines.append("Даты CPI за 2021–2025 получить не удалось (BLS отдаёт только текущий график) — раздел пропущен.")
        lines.append("")
    return "\n".join(lines), res


def simulate_weights(atlas: Atlas, weights_fn, weeks: Sequence[int]) -> List[mx.Week]:
    """Недельный прогон произвольных весов (та же арифметика издержек и фандинга, что в И15)."""
    out: List[mx.Week] = []
    held: Dict[str, float] = {}
    cost = ws.TAKER_FEE + ws.SLIPPAGE
    for t, t_next in zip(weeks, weeks[1:]):
        target = weights_fn(t, t_next)
        turnover = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        gross = funding = 0.0
        new_held: Dict[str, float] = {}
        for s, w in target.items():
            ser = atlas.data[s]
            p0, p1 = ser.open[t], ser.open[t_next]
            gross += w * (p1 / p0 - 1)
            for i in range(bisect.bisect_right(ser.fund_ts, t), bisect.bisect_right(ser.fund_ts, t_next)):
                funding += w * ser.fund_rate[i]
            new_held[s] = w * p1 / p0
        out.append(mx.Week(t, t_next, gross - turnover * cost - funding, gross, turnover * cost, funding,
                           tuple(s for s, w in target.items() if w > 0), tuple(s for s, w in target.items() if w < 0)))
        held = new_held
    return out


def by_year(weeks: Sequence[mx.Week]) -> List[str]:
    rows = []
    for year in range(2021, 2027):
        rs = [w.r for w in weeks if datetime.fromtimestamp(w.entry_t / 1000, UTC).year == year]
        if len(rs) >= 4:
            rows.append(f"{year}: {100 * mean(rs):+.2f} % ({len(rs)} нед.)")
    return rows


def week_stats(weeks: Sequence[mx.Week]) -> str:
    rs = [w.r for w in weeks]
    if len(rs) < 10:
        return "мало недель"
    lo, hi = boot_ci([[r] for r in rs])
    sh = mean(rs) / sd(rs) * math.sqrt(52) if sd(rs) else float("nan")
    return (f"{len(rs)} нед., средняя {100 * mean(rs):+.2f} % [{100 * lo:+.2f}; {100 * hi:+.2f}], Sharpe {sh:.2f}/год, "
            f"просадка {100 * mx.report.max_drawdown(rs):.1f} %, издержки {100 * mean([w.costs for w in weeks]):.2f} %, "
            f"фандинг {100 * mean([w.funding for w in weeks]):+.3f} % в неделю")


def section_candidates(atlas: Atlas, weeks: Sequence[int]) -> Tuple[str, Dict]:
    lines = ["## 8. Что из этого выглядит как правило — три кандидата с издержками (подсмотрено здесь же, не принимается)", ""]
    res = {}

    def tradable(t, t_next, syms):
        return [s for s in syms if atlas.data[s].open.get(t) and atlas.data[s].open.get(t_next)]

    # 8а. Лонг BTC / шорт равновзвешенных альтов top30 (без BTC), по 0,5
    def btc_vs_alts(t, t_next):
        d = t // DAY_MS
        alts = tradable(t, t_next, [s for s in atlas.top(d, 31) if s != "BTCUSDT"])[:30]
        if len(alts) < 10 or not atlas.data.get("BTCUSDT", ws.Series()).open.get(t_next):
            return {}
        return {"BTCUSDT": 0.5, **{s: -0.5 / len(alts) for s in alts}}
    w1 = simulate_weights(atlas, btc_vs_alts, weeks)
    res["btc_vs_alts"] = week_stats(w1)
    lines.append("**8а. «Альты истекают к BTC»: лонг BTC 50 % / шорт равновзвешенных 30 самых ликвидных альтов 50 %, неделя.** " + week_stats(w1))
    lines.append("")
    lines.append("По годам: " + "; ".join(by_year(w1)) + ".")
    lines.append("")

    # 8б. Шорт самых отрицательных по фандингу (нижние 10 % вселенной, не меньше 5), валовая 1
    def short_neg_funding(t, t_next):
        d = t // DAY_MS
        vals = []
        for s in tradable(t, t_next, atlas.universe.get(d, [])):
            ser = atlas.data[s]
            lo, hi = bisect.bisect_left(ser.fund_ts, t - 3 * DAY_MS), bisect.bisect_left(ser.fund_ts, t)
            if hi - lo >= 3:
                vals.append((mean(ser.fund_rate[lo:hi]), s))
        if len(vals) < 20:
            return {}
        vals.sort()
        n = max(5, len(vals) // 10)
        return {s: -1.0 / n for _, s in vals[:n]}
    w2 = simulate_weights(atlas, short_neg_funding, weeks)
    res["short_neg_funding"] = week_stats(w2)
    lines.append("**8б. Шорт «самых отрицательных по фандингу» (нижний дециль вселенной по средней ставке за 3 дня), валовая 1, неделя.** " + week_stats(w2))
    lines.append("")
    lines.append("По годам: " + "; ".join(by_year(w2)) + ".")
    lines.append("")

    def short_neg_plus_btc(t, t_next):
        w = short_neg_funding(t, t_next)
        if not w or not atlas.data["BTCUSDT"].open.get(t_next):
            return {}
        return {**{s: v * 0.5 for s, v in w.items()}, "BTCUSDT": 0.5}
    w3 = simulate_weights(atlas, short_neg_plus_btc, weeks)
    res["short_neg_plus_btc"] = week_stats(w3)
    lines.append("**8в. То же, но рыночно-нейтрально: шорт нижнего дециля 50 % + лонг BTC 50 %.** " + week_stats(w3))
    lines.append("")
    lines.append("По годам: " + "; ".join(by_year(w3)) + ".")
    lines.append("")

    # 8г. Новые листинги: накопленная альфа к BTC по дням после первой свечи
    curve: Dict[int, List[Tuple[int, float]]] = defaultdict(list)
    n_list = 0
    for s, ser in atlas.data.items():
        if ser.first_day is None or ser.first_day < atlas.d0 + 1:
            continue
        n_list += 1
        cum = 0.0
        for k in range(1, 31):
            d = ser.first_day + k
            r, b = atlas.ret[s].get(d), atlas.btc.get(d)
            if r is None or b is None:
                break
            cum += r - b
            curve[k].append((d, cum))
    pts = []
    for k in (3, 7, 14, 30):
        st = summarize(curve[k])
        res[f"listing|{k}"] = st
        pts.append(f"день {k}: {st['mean']:+.1f} % [{st['lo']:+.1f}; {st['hi']:+.1f}] (n={st['n']})")
    lines.append(f"**8г. Новые листинги ({n_list} контрактов, запущенных в периоде): накопленная альфа к BTC после первой свечи.** " + "; ".join(pts) + ".")
    lines.append("")
    lines.append("Все четыре — гипотезы, подсмотренные на всех данных. Проверять их на этих же данных нельзя; правило записывается в план и наблюдается вперёд.")
    lines.append("")
    return "\n".join(lines), res


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Атлас закономерностей")
    parser.add_argument("--db", default=str(history.DEFAULT_DB.with_name("history_wide.db")))
    parser.add_argument("--out", default="docs/MARKET_ATLAS.md")
    parser.add_argument("--json")
    parser.add_argument("--cpi", help="файл со списком дат CPI YYYY-MM-DD")
    args = parser.parse_args(argv)
    cpi = [x.strip() for x in open(args.cpi, encoding="utf-8")] if args.cpi else []
    cpi = [x for x in cpi if x]
    conn = history.connect(args.db)
    start_ms = day_ms(START)
    last = conn.execute("SELECT MAX(ts) FROM candles WHERE interval = '4h' AND symbol = 'BTCUSDT'").fetchone()[0]
    end_ms = int(last) + H4_MS
    data = ws.load(conn, start_ms, end_ms)
    bars_1h: Dict[str, ws.Series] = {}
    for (sym,) in conn.execute("SELECT DISTINCT symbol FROM candles WHERE interval = '1h'"):
        s = ws.Series()
        for ts, o, turn in conn.execute("SELECT ts, open, turnover FROM candles WHERE symbol = ? AND interval = '1h' ORDER BY ts", (sym,)):
            s.open[int(ts)] = float(o)
            s.turnover[int(ts)] = float(turn or 0.0)
        bars_1h[sym] = s
    conn.close()
    atlas = Atlas(data, start_ms, end_ms)
    weeks = mx.mondays(start_ms, end_ms)
    parts, allres = [], {}
    head = [f"# Атлас закономерностей рынка бессрочных контрактов Bybit (разведка, {datetime.now(UTC):%d.%m.%Y})", "",
            "**Что это.** Разведочный разбор всех собранных данных: 393 крипто-контракта с 2021 года (4h-свечи, фандинг), "
            "1h по 40 самым ликвидным, календарь решений ФРС. Каждая цифра подсмотрена на всех данных, включая уже открытый "
            "отложенный конец, поэтому **ничего из атласа не принимается как стратегия** — это карта, по которой записываются "
            "гипотезы для наблюдения вперёд (данные после 14.09.2026). Интервалы — 95 %, бутстреп по дням; издержки не вычтены, "
            "если не сказано иное.", "",
            f"Период {datetime.fromtimestamp(start_ms / 1000, UTC):%d.%m.%Y}–{datetime.fromtimestamp(end_ms / 1000, UTC):%d.%m.%Y}, "
            f"контрактов {len(data)}, вселенная (≥ 100 дней истории, оборот ≥ 2 млн/день за 30 дн) — по дням.", ""]
    parts.append("\n".join(head))
    for fn, kw in ((section_regimes, {"weeks": weeks}), (section_calendar, {"bars_1h": bars_1h, "hourly": hourly_stats(bars_1h, atlas.d0, atlas.d1)}),
                   (section_funding, {}), (section_age, {}), (section_leadlag, {}), (section_tails, {}), (section_macro, {"cpi": cpi}),
                   (section_candidates, {"weeks": weeks})):
        text, res = fn(atlas, **kw)
        parts.append(text)
        allres[fn.__name__] = res
        print(fn.__name__, "ok", flush=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(allres, f, ensure_ascii=False, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
