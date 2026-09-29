"""
И18 на бумаге — наблюдение вперёд с 21.09.2026: `python -m backtest.h18 --end <понедельник>`.
Правило и критерии — docs/TRADER_PLAN.md, И18. Заморожено отдельным модулем 29.09.2026 (аудит): до
этого правило жило вложенной функцией в atlas.section_candidates и копией в listings.h18_weeks, а кода
критериев не было вовсе — его пришлось бы писать, уже видя недели.

Правило: понедельник 00:00 UTC, вселенная И15 (≥ 100 дней истории, оборот ≥ 2 млн/день за 30 дн),
лонг BTC 0,5 / шорт 30 крупнейших по обороту альтов по −0,5/30 (меньше 10 альтов — позиции нет); вход
и выход по open 4h-бара понедельника; издержки и фандинг — atlas.simulate_weights.

Критерии (26 недель — 16.03.2027): средняя ≥ +0,15 %; нижняя граница 95 % ≥ −0,10 %; просадка ≤ 15 %.
Остановка раньше — средняя < −2 SE. Определения зафиксированы здесь, до данных:
• SE = стандартное отклонение недель (n − 1) / √n; нижняя граница 95 % = средняя − 1,96 × SE;
• просадка — наибольшее падение накопленной суммы недельных результатов от её максимума (капитал
  фиксирован, как у демо), в долях капитала.
"""
import argparse
import math
from typing import Callable, Dict, List, Sequence

from backtest import momentum_xs as mx

BTC = "BTCUSDT"
ALTS = 30
MIN_ALTS = 10
START = "2026-09-21"
MEAN_MIN = 0.0015
LOWER_MIN = -0.0010
DRAWDOWN_MAX = 0.15


def btc_vs_alts(atlas) -> Callable[[int, int], Dict[str, float]]:
    """Веса недели по правилу И18 (правило из atlas, 8а — один источник для атласа, И21 и наблюдения)."""
    def weights(t: int, t_next: int) -> Dict[str, float]:
        d = t // mx.DAY_MS
        alts = [s for s in atlas.top(d, ALTS + 1) if s != BTC
                and atlas.data[s].open.get(t) and atlas.data[s].open.get(t_next)][:ALTS]
        btc = atlas.data.get(BTC)
        if len(alts) < MIN_ALTS or btc is None or not btc.open.get(t_next):
            return {}
        return {BTC: 0.5, **{s: -0.5 / len(alts) for s in alts}}
    return weights


def criteria(returns: Sequence[float]) -> Dict:
    n = len(returns)
    if n < 2:
        return {"weeks": n, "passed": False, "stop": False}
    mean = sum(returns) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (n - 1))
    se = sd / math.sqrt(n)
    peak = cum = dd = 0.0
    for r in returns:
        cum += r
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    lower = mean - 1.96 * se
    return {"weeks": n, "mean": mean, "se": se, "lower95": lower, "drawdown": dd,
            "passed": mean >= MEAN_MIN and lower >= LOWER_MIN and dd <= DRAWDOWN_MAX,
            "stop": mean < -2 * se}


def weeks(db: str, start_ms: int, end_ms: int) -> List[mx.Week]:
    from backtest import atlas as at
    from backtest import history
    from backtest import wide_search as ws
    conn = history.connect(db)
    data = ws.load(conn, start_ms - 40 * mx.DAY_MS, end_ms)
    atl = at.Atlas(data, start_ms - 40 * mx.DAY_MS, end_ms)
    return at.simulate_weights(atl, btc_vs_alts(atl), mx.mondays(start_ms, end_ms))


def main(argv=None) -> int:
    from backtest import history
    parser = argparse.ArgumentParser(description="И18 на бумаге: недели и критерии")
    parser.add_argument("--db", default=str(history.DEFAULT_DB), help="широкий кэш (history_wide, --min-age-days 100)")
    parser.add_argument("--start", default=START)
    parser.add_argument("--end", required=True, help="последний понедельник-выход ГГГГ-ММ-ДД")
    args = parser.parse_args(argv)
    ws_ = weeks(args.db, mx.day_ms(args.start), mx.day_ms(args.end))
    c = criteria([w.r for w in ws_])
    for w in ws_:
        print(f"  {w.entry_t}: {100 * w.r:+.2f} %")
    if c["weeks"] < 2:
        print("И18: недель меньше двух")
        return 0
    print(f"И18 на бумаге: {c['weeks']} нед., средняя {100 * c['mean']:+.3f} %, SE {100 * c['se']:.3f} %, "
          f"нижняя граница {100 * c['lower95']:+.3f} %, просадка {100 * c['drawdown']:.2f} % → "
          + ("ОСТАНОВКА (средняя < −2 SE)" if c["stop"] else ("критерии пройдены" if c["passed"] else "критерии не пройдены")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
