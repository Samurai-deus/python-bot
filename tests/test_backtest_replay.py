"""
Прогон сетапов по истории (Ф1 плана трейдера, шаг 3, фазы 0 и A): окна без подглядывания
вперёд, контекст «последний на момент t», и главное — фаза A даёт те же сетапы, что живой
генератор на тех же окнах (заглушки побочных эффектов — из эталонного теста).
"""
import math
import random

import pytest

from backtest import history, replay
from tests import test_setup_equivalence as eq

T0 = 1_725_000_000_000
FIVE = replay.FIVE_MS
PER_TF = {"5m": 1, "15m": 3, "30m": 6, "1h": 12, "4h": 48}


def fill(conn, symbol, seed, drift, vol, count):
    """Синтетические 5m свечи и их агрегаты — в кэш истории, как их положил бы загрузчик."""
    rng = random.Random(seed)
    price, base = 100.0, []
    for i in range(count):
        o = price
        c = o * math.exp(drift + vol * rng.gauss(0, 1))
        h, low = max(o, c) * (1 + abs(rng.gauss(0, vol / 2))), min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        base.append((T0 + i * FIVE, o, h, low, c, 1000 * (1 + abs(rng.gauss(0, 0.5)))))
        price = c
    for tf, n in PER_TF.items():
        for j in range(0, count - count % n, n):
            chunk = base[j:j + n]
            conn.execute("INSERT INTO candles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (symbol, tf, chunk[0][0], chunk[0][1], max(r[2] for r in chunk), min(r[3] for r in chunk),
                          chunk[-1][4], sum(r[5] for r in chunk), sum(r[5] * r[4] for r in chunk)))
    conn.commit()


@pytest.fixture
def conn(tmp_path):
    connection = history.connect(tmp_path / "history.db")
    yield connection
    connection.close()


def test_a_window_holds_only_bars_closed_by_the_moment(conn):
    fill(conn, "ADAUSDT", 1, 0.0005, 0.003, 48 * 130)
    series = replay.load_series(conn, ["ADAUSDT"])
    t = T0 + 48 * 125 * FIVE + 7 * FIVE  # внутри 4h-бара и внутри 15m-бара
    for tf, n in PER_TF.items():
        window = series[("ADAUSDT", tf)].window(t)
        step = n * FIVE
        assert all(int(r[0]) + step <= t for r in window), tf
        assert int(window[-1][0]) + 2 * step > t, f"{tf}: последний закрытый бар — не старый"
        assert len(window) == min(replay.BARS, len(window))


def test_the_context_is_the_last_one_not_after_the_moment():
    contexts = [replay.Context(t, "TREND", {}) for t in (0, 900_000, 1_800_000)]
    assert replay.context_at(contexts, 899_999).t_ms == 0
    assert replay.context_at(contexts, 900_000).t_ms == 900_000
    assert replay.context_at(contexts, -1) is None
    keys = [c.t_ms for c in contexts]  # готовый список меток — тот же ответ
    assert replay.context_at(contexts, 899_999, keys).t_ms == 0
    assert replay.context_at(contexts, 1_800_001, keys).t_ms == 1_800_000
    assert replay.context_at(contexts, -1, keys) is None


def test_phase_a_finds_the_same_setups_as_the_live_generator(conn, monkeypatch):
    symbols = ["BTCUSDT", "ETHUSDT", "ADAUSDT"]
    for k, (drift, vol) in enumerate([(0.0012, 0.003), (-0.0012, 0.004), (0.0, 0.006)]):
        fill(conn, symbols[k], 10 + k, drift, vol, 48 * 140)
    series = replay.load_series(conn, symbols)
    start, end = T0 + 48 * 125 * FIVE, T0 + 48 * 139 * FIVE
    moments = list(range(start, end, 12 * FIVE))

    # Живой генератор на тех же окнах (заглушки — из эталонного теста)
    live = eq.run_scenarios(monkeypatch, markets=((t, replay.candles_at(series, symbols, t)) for t in moments),
                            symbols=symbols)
    live_setups, t_now = set(), None
    for e in live:
        if e[0] == "SCENARIO":
            t_now = e[1]
        elif e[0] == "GK":
            live_setups.add((t_now, e[1], e[2], round(e[3], 8), round(e[4], 8), round(e[5], 8), e[6]))

    replayed = set()
    for symbol in symbols:
        events, _ = replay.replay_symbol(conn, series, symbol, start, end, contexts=None, with_micro=False,
                                         every_ms=12 * FIVE)
        replayed |= {(e.t_ms, e.symbol, e.side, round(e.entry, 8), round(e.stop, 8), round(e.target, 8), e.strategy)
                     for e in events}
    assert live_setups, "сценарий без сетапов ничего не проверяет"
    assert replayed == live_setups


def test_the_setups_cache_gives_the_same_events_without_recomputing(conn, tmp_path, monkeypatch):
    from backtest import run
    fill(conn, "BTCUSDT", 10, 0.0012, 0.003, 48 * 140)
    (tmp_path / "cache").mkdir()
    start, end = T0 + 48 * 125 * FIVE, T0 + 48 * 139 * FIVE
    args = (str(tmp_path / "history.db"), "BTCUSDT", start, end, None, False, str(tmp_path / "cache"))
    symbol, first, skips = run._symbol_setups(args)
    assert first, "без сетапов кэш ничего не проверяет"
    monkeypatch.setattr(replay, "replay_symbol", lambda *a, **k: pytest.fail("прогон повторён, кэш не использован"))
    assert run._symbol_setups(args) == (symbol, first, skips)
