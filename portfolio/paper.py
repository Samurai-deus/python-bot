"""
Критерий честности исполнения И14 (итог 14.12.2026): `python -m portfolio.paper --db <portfolio.db>`.
Записано и заморожено 29.09.2026, до данных срока (аудит: кода для бумаги не было — его пришлось бы
писать, уже видя счёт, и выбор деталей расчёта стал бы свободой аналитика).

Правило И14: результат на счёте не хуже результата тех же сигналов на бумаге минус 1,5 % капитала.

• Бумага — цели, которые исполнитель посчитал в понедельник t: веса первого прогона недели в
  rebalance_runs. Тот же код сигналов и тот же набор монет, что на счёте, — расхождение состава
  исключено построением; сравнение меряет только исполнение.
• Исполнение на бумаге идеальное: по открытию 4h-бара понедельника 00:00 UTC; издержки
  (TAKER_FEE + SLIPPAGE) на оборот после дрейфа цены; фандинг по опубликованным ставкам — та же
  арифметика, что backtest.trend_ts.simulate (тест сверяет их на одних данных).
• Капитал фиксирован, как у исполнителя (цель = вес × капитал): результат в USDT = капитал × сумма
  недельных доходностей. Издержек выхода в конце нет: счёт на конец срока позиции не закрывает.
• Счёт — изменение стоимости USDT-части: последний снимок до первой ребалансировки периода против
  последнего снимка до конца периода. Внешние движения средств уже вычтены из снимков (portfolio.adjust).
"""
import argparse
import bisect
import json
import sqlite3
from datetime import UTC, datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from backtest import momentum_xs as mx
from backtest.portfolio import SLIPPAGE, TAKER_FEE

HONESTY_SLACK = 0.015           # правило И14: «минус 1,5 % капитала»
FIRST_WEEK = "2026-09-21"       # неделя 14–21.09 (догоняющая) в критерий не входит — поправка 14.09
LAST_WEEK = "2026-12-07"        # 12 ребалансировок; итог 14.12


def week_weights(conn: sqlite3.Connection, weeks_t: Sequence[int]) -> Dict[int, Dict[str, float]]:
    """Цели первого прогона каждой недели (понедельник t → {символ: вес}); недели без прогона — пусто."""
    out = {}
    for t in weeks_t:
        row = conn.execute("SELECT weights FROM rebalance_runs WHERE t = ? ORDER BY done_ms LIMIT 1", (t,)).fetchone()
        out[t] = json.loads(row[0]) if row else {}
    return out


def paper_weeks(weights: Dict[int, Dict[str, float]], data: Dict, weeks_t: Sequence[int]) -> List[Tuple[int, float]]:
    """
    Доходность бумаги по неделям (доли капитала): weeks_t — понедельники периода плюс конец периода.
    Арифметика backtest.trend_ts.simulate без издержек выхода.
    """
    out = []
    held: Dict[str, float] = {}
    for t, t_next in zip(weeks_t, weeks_t[1:]):
        target = weights.get(t, {})
        turnover = sum(abs(target.get(s, 0.0) - held.get(s, 0.0)) for s in set(target) | set(held))
        r = -turnover * (TAKER_FEE + SLIPPAGE)
        new_held: Dict[str, float] = {}
        for s, w in target.items():
            p0, p1 = data[s]["o4"][t], data[s]["o4"][t_next]
            r += w * (p1 / p0 - 1)
            f = data[s]["fund"]
            for i in range(bisect.bisect_right(f["ts"], t), bisect.bisect_right(f["ts"], t_next)):
                r -= w * f["rate"][i] * data[s]["o4"].get(f["ts"][i], p0) / p0     # лонг платит положительную
            new_held[s] = w * p1 / p0
        out.append((t, r))
        held = new_held
    return out


def account_change(conn: sqlite3.Connection, start_ms: int, end_ms: int) -> Optional[float]:
    """Стоимость: последний снимок до end_ms минус последний снимок до start_ms (None — снимков нет)."""
    def before(ts: int):
        row = conn.execute("SELECT equity FROM snapshots WHERE ts < ? ORDER BY ts DESC LIMIT 1", (ts,)).fetchone()
        return float(row[0]) if row else None
    a, b = before(start_ms), before(end_ms)
    return None if a is None or b is None else b - a


def verdict(account_usdt: float, paper_usdt: float, capital: float) -> bool:
    return account_usdt >= paper_usdt - HONESTY_SLACK * capital


def evaluate(conn: sqlite3.Connection, capital: float, load: Callable[[Sequence[str], int, int], Dict],
             first: str = FIRST_WEEK, last: str = LAST_WEEK) -> Dict:
    weeks_t = mx.mondays(mx.day_ms(first), mx.day_ms(last)) + [mx.day_ms(last) + mx.WEEK_MS]
    weights = week_weights(conn, weeks_t[:-1])
    symbols = sorted({s for w in weights.values() for s in w})
    data = load(symbols, weeks_t[0], weeks_t[-1])
    weeks = paper_weeks(weights, data, weeks_t)
    paper = capital * sum(r for _, r in weeks)
    account = account_change(conn, weeks_t[0], weeks_t[-1])
    # Второй критерий правила: «недель с непройденной ребалансировкой — не больше 1». Неделя пройдена, если
    # хоть один её прогон закончился без непрошедших ордеров.
    unfinished = [t for t in weeks_t[:-1] if not conn.execute(
        "SELECT 1 FROM rebalance_runs WHERE t = ? AND failed = '[]'", (t,)).fetchone()]
    return {"weeks": weeks, "paper_usdt": paper, "account_usdt": account, "unfinished_weeks": unfinished,
            "honest": account is not None and verdict(account, paper, capital),
            "halted": conn.execute("SELECT value FROM state WHERE key = 'halted'").fetchone() is not None}


def history_loader(db: str) -> Callable[[Sequence[str], int, int], Dict]:
    """Свечи 4h и ставки фандинга Bybit в локальный кэш истории и из него — как у наблюдения И4/И3."""
    def load(symbols, start_ms, end_ms):
        from backtest import history
        from backtest import trend_ts as tt
        conn = history.connect(db)
        api = history.BybitHistory()
        for s in symbols:
            history.sync_candles(conn, api, s, "4h", start_ms - mx.DAY_MS, end_ms + mx.H4_MS)
            history.sync_funding(conn, api, s, start_ms, end_ms)
        data = tt.load(conn, symbols, start_ms, end_ms)
        conn.close()
        return data
    return load


def main(argv=None) -> int:
    from backtest import history
    parser = argparse.ArgumentParser(description="И14: критерий честности исполнения (счёт против бумаги)")
    parser.add_argument("--db", required=True, help="база исполнителя (portfolio.db)")
    parser.add_argument("--capital", type=float, default=1000.0)
    parser.add_argument("--history", default=str(history.DEFAULT_DB))
    parser.add_argument("--first", default=FIRST_WEEK)
    parser.add_argument("--last", default=LAST_WEEK)
    args = parser.parse_args(argv)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    res = evaluate(conn, args.capital, history_loader(args.history), args.first, args.last)
    for t, r in res["weeks"]:
        print(f"  {datetime.fromtimestamp(t / 1000, UTC):%d.%m.%Y}: бумага {100 * r:+.2f} % ({args.capital * r:+.2f} USDT)")
    acc = res["account_usdt"]
    if acc is None:
        print("И14: нет снимков счёта")
        return 1
    passed = res["honest"] and not res["halted"] and len(res["unfinished_weeks"]) <= 1
    print(f"И14: счёт {acc:+.2f} USDT, бумага {res['paper_usdt']:+.2f} USDT, допуск {HONESTY_SLACK * args.capital:.2f} — "
          f"исполнение {'честное' if res['honest'] else 'НЕ честное'}; стоп {'сработал' if res['halted'] else 'нет'}; "
          f"непройденных недель {len(res['unfinished_weeks'])} → {'проходит' if passed else 'НЕ проходит'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
