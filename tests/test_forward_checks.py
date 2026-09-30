"""
Код оценки проверок вперёд, замороженный 30.09.2026 до данных: И10 / И10б (новости, 06.12) и И19б (межбиржевой
фандинг по расчётной ставке, 19.10 и 16.11). Данные синтетические, сети нет.
"""
import sqlite3

import pytest

from backtest import h10, h19b
from backtest import xfunding as xf

MIN = h10.MINUTE_MS
HOUR = h10.HOUR_MS
DAY = h10.DAY_MS
T0 = h10.day_ms("2026-09-14")


# ---------------------------------------------------------------------------
# И10 / И10б
# ---------------------------------------------------------------------------

@pytest.fixture
def news_db():
    import database
    conn = sqlite3.connect(":memory:")
    database._ensure_news_tables(conn.cursor())
    return conn


def add_item(conn, uid, scored_ms, *, backlog=0, status="ok", prompt=h10.PROMPT_I10, model="anthropic/claude-sonnet-5"):
    conn.execute("INSERT INTO news_items VALUES (?, 'src', 'title', NULL, NULL, ?, ?, ?, ?, ?, ?)",
                 (uid, scored_ms - MIN, backlog, scored_ms, status, model, prompt))


def add_score(conn, uid, symbol, direction="up", magnitude=2, confidence=0.6, novelty=1):
    conn.execute("INSERT INTO news_scores VALUES (?, ?, ?, ?, 'hours', ?, ?)",
                 (uid, symbol, direction, magnitude, confidence, novelty))


def add_blind(conn, uid, scored_ms, direction="up", magnitude=2, confidence=0.6, novelty=1, status="ok",
              prompt=h10.PROMPT_I10B, model="anthropic/claude-sonnet-5"):
    conn.execute("INSERT INTO news_blind VALUES (?, 'blind', ?, ?, ?, ?, ?, ?, 'hours', ?, ?)",
                 (uid, scored_ms, status, model, prompt, direction, magnitude, confidence, novelty))


def test_the_written_rule_is_what_the_code_uses():
    assert (h10.MIN_MAGNITUDE, h10.MIN_CONFIDENCE, h10.COST, h10.MIN_SIGNALS, h10.MIN_MEAN) == (2, 0.6, 0.0031, 100, 0.002)
    assert h10.EXITS_MS == {"1h": HOUR, "4h": 4 * HOUR} and h10.ALPHA == 0.025 and h10.REPEAT_MS == HOUR
    assert (h10.PROMPT_I10, h10.PROMPT_I10B) == ("news-v1", "news-blind-v1")
    from news import blind, scorer
    assert scorer.PROMPT_VERSION == h10.PROMPT_I10 and blind.PROMPT_VERSION == h10.PROMPT_I10B
    assert scorer.DEFAULT_MODEL.startswith(h10.MODEL_PREFIX)


def test_i10_signal_needs_novelty_strength_confidence_and_direction(news_db):
    c = news_db
    for i, kw in enumerate([{}, {"novelty": 0}, {"magnitude": 1}, {"confidence": 0.59}, {"direction": "none"}]):
        add_item(c, f"u{i}", T0 + i * HOUR)
        add_score(c, f"u{i}", "SOL", **kw)
    add_item(c, "backlog", T0, backlog=1)
    add_score(c, "backlog", "SOL")
    add_item(c, "other-prompt", T0, prompt="news-v2")
    add_score(c, "other-prompt", "SOL")
    add_item(c, "other-model", T0, model="openai/gpt-x")
    add_score(c, "other-model", "SOL")
    add_item(c, "down", T0 + 9 * HOUR)
    add_score(c, "down", "ETH", direction="down", magnitude=3, confidence=0.9)
    sig = h10.load_i10(c)
    assert [(s.uid, s.side) for s in sig] == [("u0", 1), ("down", -1)]


def test_i10b_takes_coins_from_the_first_score_and_judgement_from_the_blind_one(news_db):
    c = news_db
    add_item(c, "a", T0)
    add_score(c, "a", "SOL", direction="up", magnitude=0, confidence=0.1, novelty=0)   # первая оценка слабая
    add_score(c, "a", "ETH", direction="none")
    add_blind(c, "a", T0 + 5 * MIN, direction="down", magnitude=3, confidence=0.8)
    add_item(c, "stale", T0)
    add_score(c, "stale", "BTC")
    add_blind(c, "stale", T0 + 40 * MIN, status="stale")
    sig = h10.load_i10b(c)
    assert {(s.ticker, s.side, s.t_ms, s.first_ms) for s in sig} == {("SOL", -1, T0 + 5 * MIN, T0), ("ETH", -1, T0 + 5 * MIN, T0)}
    assert h10.both_scored(c) == {"a"}


def test_repeats_within_an_hour_of_the_last_accepted_signal_are_dropped():
    s = lambda uid, t, tk="SOL": h10.Signal(uid, tk, 1, t, t)  # noqa: E731
    out = h10.dedup([s("a", T0), s("b", T0 + 30 * MIN), s("c", T0 + 59 * MIN), s("d", T0 + 60 * MIN),
                     s("e", T0 + 90 * MIN), s("x", T0 + 10 * MIN, "ETH")])
    assert [x.uid for x in out] == ["a", "x", "d"]


def test_entry_is_the_first_minute_starting_at_least_a_minute_after_the_score():
    assert h10.entry_minute(T0) == T0 + MIN
    assert h10.entry_minute(T0 + 30_000) == T0 + 2 * MIN
    assert h10.entry_minute(T0 + MIN - 1) == T0 + 2 * MIN


class FakeMarket(h10.Market):
    def __init__(self, prices, listed=None):
        self.prices = prices                  # (symbol, t) → open
        self.listed = listed or {"SOLUSDT": 0, "ETHUSDT": 0, "1000PEPEUSDT": 0}

    def symbol_for(self, ticker, t_ms):
        for sym in (f"{ticker}USDT", f"1000{ticker}USDT"):
            if sym in self.listed and self.listed[sym] <= t_ms:
                return sym
        return None

    def minute_open(self, symbol, t_ms):
        return self.prices.get((symbol, t_ms))


def test_trade_result_is_side_times_move_minus_round_trip_costs():
    t = T0
    e = h10.entry_minute(t)
    m = FakeMarket({("SOLUSDT", e): 100.0, ("SOLUSDT", e + HOUR): 102.0,
                    ("1000PEPEUSDT", e): 10.0, ("1000PEPEUSDT", e + HOUR): 9.0})
    tr, skipped = h10.trades([h10.Signal("a", "SOL", 1, t, t), h10.Signal("b", "PEPE", -1, t, t),
                              h10.Signal("c", "ETH", 1, t, t), h10.Signal("d", "NOPE", 1, t, t)], m, HOUR)
    assert [round(x["ret"], 6) for x in tr] == [round(0.02 - 0.0031, 6), round(0.10 - 0.0031, 6)]
    assert tr[1]["symbol"] == "1000PEPEUSDT"
    assert skipped == {"no_contract": 1, "no_price": 1}


def test_a_contract_listed_after_the_score_does_not_count():
    m = FakeMarket({}, listed={"SOLUSDT": T0 + DAY})
    assert m.symbol_for("SOL", T0) is None


def _trades(rets, start=T0, step=DAY):
    return [{"ret": r, "t_entry": start + i * step, "day": (start + i * step) // DAY, "first_day": (start + i * step) // DAY,
             "side": 1} for i, r in enumerate(rets)]


def test_criteria_by_the_written_definitions():
    end = T0 + 120 * DAY
    good = h10.criteria(_trades([0.006, 0.004] * 60), T0, end)
    assert good["passed"] and good["n"] == 120
    few = h10.criteria(_trades([0.006, 0.004] * 40), T0, end)
    assert not few["passed"] and not few["checks"]["сигналов ≥ 100"]
    small = h10.criteria(_trades([0.0025, 0.0005] * 60), T0, end)
    assert small["ci97_5"][0] > 0 and not small["checks"]["средняя ≥ +0,2 % номинала"]
    late = h10.criteria(_trades([-0.01] * 80 + [0.05] * 40), T0, end)       # плюс только в последней трети
    assert late["mean"] > h10.MIN_MEAN and not late["checks"]["плюс в 2 из 3 отрезков"]


def test_bootstrap_resamples_whole_days():
    same_day = _trades([0.01, -0.01, 0.03], step=MIN)
    lo, hi = h10.boot_mean(same_day)
    assert lo == hi == pytest.approx(0.01)


def _market_for(signals, ret_1h, ret_4h, sym="SOLUSDT"):
    prices = {}
    for s in signals:
        e = h10.entry_minute(s.t_ms)
        prices[(sym, e)] = 100.0
        prices[(sym, e + HOUR)] = 100.0 * (1 + s.side * ret_1h)
        prices[(sym, e + 4 * HOUR)] = 100.0 * (1 + s.side * ret_4h)
    return FakeMarket(prices)


def test_a_branch_passes_if_one_exit_variant_passes_all_criteria():
    sig = [h10.Signal(f"u{i}", "SOL", 1 if i % 3 else -1, T0 + i * 7 * HOUR, T0 + i * 7 * HOUR) for i in range(150)]
    end = T0 + 60 * DAY
    res = h10.branch(sig, _market_for(sig, 0.01, -0.01), T0, end)
    assert res["variants"]["1h"]["passed"] and not res["variants"]["4h"]["passed"] and res["passed"]
    assert res["best_lower"] == res["variants"]["1h"]["ci97_5"][0]
    assert res["variants"]["1h"]["down"]["n"] + res["variants"]["1h"]["up"]["n"] == res["variants"]["1h"]["n"]


def test_signals_whose_four_hour_exit_is_after_the_check_are_left_out():
    end = T0 + DAY
    sig = [h10.Signal("in", "SOL", 1, end - 5 * HOUR, end - 5 * HOUR), h10.Signal("out", "ETH", 1, end - 3 * HOUR, end - 3 * HOUR)]
    assert h10.branch(sig, FakeMarket({}), T0, end)["signals"] == 1


def test_paired_difference_confirms_when_the_blind_branch_is_better():
    uids = {f"u{i}" for i in range(60)}
    a = [h10.Signal(f"u{i}", "SOL", 1, T0 + i * DAY, T0 + i * DAY) for i in range(60)]
    b = [h10.Signal(f"u{i}", "SOL", 1, T0 + i * DAY + 5 * MIN, T0 + i * DAY) for i in range(60)]
    prices = {}
    for s in a:
        e = h10.entry_minute(s.t_ms)
        prices.update({("SOLUSDT", e): 100.0, ("SOLUSDT", e + HOUR): 100.0, ("SOLUSDT", e + 4 * HOUR): 100.0})
    for i, s in enumerate(b):
        e = h10.entry_minute(s.t_ms)
        up = 1.01 + 0.002 * (i % 3)
        prices.update({("SOLUSDT", e): 100.0, ("SOLUSDT", e + HOUR): 100.0 * up, ("SOLUSDT", e + 4 * HOUR): 100.0 * up})
    res = h10.paired(a, b, uids, FakeMarket(prices), T0 + 70 * DAY)
    assert res["confirmed"] and res["1h"]["ci97_5"][0] > 0
    same = h10.paired(a, a, uids, FakeMarket(prices), T0 + 70 * DAY)
    assert not same["confirmed"]


def test_checks_only_on_the_written_dates(news_db):
    add_item(news_db, "a", T0)
    add_score(news_db, "a", "SOL")
    dates = h10.check_dates(T0)
    assert dates[0] == h10.day_ms("2026-12-06") and all(b - a == 28 * DAY for a, b in zip(dates, dates[1:]))
    assert dates[-1] <= T0 + 26 * h10.WEEK_MS
    with pytest.raises(ValueError):
        h10.evaluate(news_db, FakeMarket({}), h10.day_ms("2026-11-30"))


def test_too_few_signals_keeps_collecting_until_the_last_check(news_db):
    add_item(news_db, "a", T0)
    add_score(news_db, "a", "SOL")
    dates = h10.check_dates(T0)
    first = h10.evaluate(news_db, FakeMarket({}), dates[0])
    assert first["i10"]["status"] == "сбор продолжается" and first["chosen"] is None
    last = h10.evaluate(news_db, FakeMarket({}), dates[-1])
    assert last["final"] and last["i10"]["status"] == "не проходит"


def test_bybit_market_resolves_listing_time_and_caches_minutes():
    calls = []

    def get(url, params):
        calls.append((url.rsplit("/", 1)[-1], params.get("status"), params.get("symbol")))
        if url.endswith("instruments-info"):
            lst = [{"symbol": "SOLUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "launchTime": "0"}] \
                if params["status"] == "Trading" else \
                [{"symbol": "1000OLDUSDT", "quoteCoin": "USDT", "contractType": "LinearPerpetual", "launchTime": str(T0)}]
            return {"result": {"list": lst, "nextPageCursor": ""}}
        a = params["start"]
        return {"result": {"list": [[str(a + i * MIN), str(100 + i), "0", "0", "0", "0", "0"] for i in range(1000)]}}

    m = h10.BybitMarket(get)
    assert m.symbol_for("SOL", T0) == "SOLUSDT"
    assert m.symbol_for("OLD", T0 - 1) is None and m.symbol_for("OLD", T0) == "1000OLDUSDT"
    block = T0 // (1000 * MIN) * 1000 * MIN
    assert m.minute_open("SOLUSDT", block + 5 * MIN) == 105.0
    assert m.minute_open("SOLUSDT", block + 7 * MIN) == 107.0
    assert sum(1 for c in calls if c[0] == "kline") == 1


# ---------------------------------------------------------------------------
# И19б
# ---------------------------------------------------------------------------

@pytest.fixture
def live_db():
    from news import xfunding_live
    conn = sqlite3.connect(":memory:")
    conn.execute(xfunding_live.SCHEMA)
    return conn


def put(conn, hour, ex, key, rate, nxt, interval=8.0, bid=100.0, ask=100.1, turnover=5e6, late=0):
    conn.execute("INSERT INTO snap VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (hour, ex, key, rate, nxt, interval, bid, ask, turnover, hour + 5 * MIN + late))


def test_h19b_uses_the_frozen_i19_rule():
    p = xf.Params()
    assert (p.entry, p.exit, p.slippage, p.max_positions, p.leg) == (0.003, 0.0005, 0.0005, 10, 1 / 20)
    assert h19b.CUTOFF == "2026-11-16" and h19b.SNAP_WINDOW_MS == 10 * MIN


def test_payments_are_seen_as_the_next_funding_time_moving_on(live_db):
    base = T0
    for h in range(0, 10):
        t = base + h * HOUR
        nxt = base + (8 * HOUR if h < 8 else 16 * HOUR)
        put(live_db, t, "bybit", "SOL", 0.0001 * (h + 1), nxt)
    data = h19b.load(live_db, base + DAY)
    # выплата в 08:00 — со ставкой последнего снимка до неё (07:05), вторая (16:00) ещё не подтверждена
    assert data["SOL"]["bybit"].payments == [(base + 8 * HOUR, pytest.approx(0.0008))]


def test_snapshots_taken_after_ten_past_are_ignored(live_db):
    put(live_db, T0, "bybit", "SOL", 0.001, T0 + HOUR, late=6 * MIN)
    put(live_db, T0, "okx", "SOL", 0.001, T0 + HOUR)
    data = h19b.load(live_db, T0 + DAY)
    assert "bybit" not in data["SOL"] and "okx" in data["SOL"]


def _two_exchange_market(conn, hours=60, drop_at=40):
    """SOL: у bitget ставка выше на 0,6 %/сутки до часа drop_at, потом разница 0; OKX — только для окна."""
    for h in range(hours):
        t = T0 + h * HOUR
        nxt = T0 + (h // 8 + 1) * 8 * HOUR
        hi = 0.002 if h < drop_at else 0.0
        put(conn, t, "bitget", "SOL", hi, nxt, bid=100.0, ask=100.1)
        put(conn, t, "bybit", "SOL", 0.0, nxt, bid=100.0, ask=100.1)
        put(conn, t, "okx", "ETH", 0.0, nxt)
        put(conn, t, "bybit", "ETH", 0.0, nxt)


def test_simulation_shorts_the_higher_rate_collects_funding_and_exits_when_it_closes(live_db):
    _two_exchange_market(live_db)
    data = h19b.load(live_db, T0 + 3 * DAY)
    start, end = h19b.window(data, T0 + 3 * DAY)
    res = h19b.simulate(data, start, end)
    [tr] = res["trades"]
    assert (tr["short"], tr["long"], tr["reason"]) == ("bitget", "bybit", "spread")
    assert tr["t0"] == T0 and tr["t1"] == T0 + 40 * HOUR
    # выплаты bitget в 08, 16, 24, 32 и 40 ч (последняя — до выхода в тот же час, со ставкой снимка 39 ч) по 0,2 %
    # на номинал 1/20, пересчитанный по середине стакана 100,05 к цене входа 100; bybit платит 0
    assert tr["funding"] == pytest.approx(5 * 0.002 / 20 * 100.05 / 100)
    # вход: шорт по bid 100, лонг по ask 100,1; выход: шорт по ask 100,1, лонг по bid 100
    assert tr["price"] == pytest.approx((1 / 20) * ((100 - 100.1) / 100 + (100 - 100.1) / 100.1))
    fees = xf.TAKER_FEE["bitget"] + xf.TAKER_FEE["bybit"] + 2 * xf.SLIPPAGE
    assert tr["costs"] == pytest.approx((1 / 20) * fees + (1 / 20) * (xf.TAKER_FEE["bitget"] + xf.SLIPPAGE) * 100.1 / 100
                                        + (1 / 20) * (xf.TAKER_FEE["bybit"] + xf.SLIPPAGE) * 100 / 100.1)


def test_an_hour_without_a_snapshot_holds_the_position(live_db):
    _two_exchange_market(live_db)
    live_db.execute("DELETE FROM snap WHERE ex = 'bybit' AND key = 'SOL' AND hour_ms = ?", (T0 + 40 * HOUR,))
    data = h19b.load(live_db, T0 + 3 * DAY)
    start, end = h19b.window(data, T0 + 3 * DAY)
    [tr] = h19b.simulate(data, start, end)["trades"]
    assert tr["t1"] == T0 + 41 * HOUR


def test_window_needs_all_three_exchanges(live_db):
    put(live_db, T0, "bybit", "SOL", 0.0, T0 + HOUR)
    put(live_db, T0, "okx", "SOL", 0.0, T0 + HOUR)
    assert h19b.window(h19b.load(live_db, T0 + DAY), T0 + DAY) is None


def test_lifetime_counts_episodes_until_the_spread_closes(live_db):
    _two_exchange_market(live_db)
    data = h19b.load(live_db, T0 + 3 * DAY)
    start, end = h19b.window(data, T0 + 3 * DAY)
    life = h19b.lifetimes(data, start, end)
    assert life["episodes"] == 1 and life["median_h"] == 40 and life["censored"] == 0


def test_evaluate_gives_the_i19_verdict_and_the_cli_refuses_other_dates(live_db, tmp_path):
    _two_exchange_market(live_db)
    res = h19b.evaluate(live_db, T0 + 3 * DAY)
    assert set(res["verdict"]["checks"]) == set(xf.verdict(res["base"], res["stress"])["checks"])
    assert res["base"]["trades"] == 1 and not res["verdict"]["passed"]      # одна сделка — меньше 50
    with pytest.raises(SystemExit):
        h19b.main(["--db", str(tmp_path / "x.db"), "--cutoff", "2026-11-10"])
