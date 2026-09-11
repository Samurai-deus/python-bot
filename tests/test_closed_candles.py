"""
Решения только по закрытым свечам (Ф1 плана трейдера, шаг 2б, 11.09.2026).

Bybit отдаёт последней незакрытую свечу; генератор решал по бару, который ещё меняется,
а на истории такого состояния нет. Теперь цикл анализа отдаёт решениям только закрытые
свечи (120 на таймфрейм, как в проверке на истории), а сырые — детектору резких движений
и кэшу цены.
"""
import pathlib
import time

import price_cache
from data_loader import closed_candles
from loops import market_analysis as analysis
from tests.test_analysis_cycle import analyse, cycle  # noqa: F401 — фикстура цикла

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIVE = 300_000
FIFTEEN = 900_000


def row(ts, close="1.0"):
    return [str(ts), "1", "2", "0.5", close, "10", "10"]


def test_the_forming_candle_is_dropped_per_timeframe():
    now = 10 * FIFTEEN + 60_000  # минута в 11-й 15m-свече и в 31-й 5m-свече
    market = {"ADAUSDT": {
        "5m": [row(i * FIVE) for i in range(31)],
        "15m": [row(i * FIFTEEN) for i in range(11)],
    }}
    closed = closed_candles(market, now)
    assert [int(r[0]) for r in closed["ADAUSDT"]["5m"]][-1] == 29 * FIVE
    assert [int(r[0]) for r in closed["ADAUSDT"]["15m"]][-1] == 9 * FIFTEEN


def test_an_already_closed_series_is_kept_and_trimmed_to_keep():
    now = 50 * FIVE
    closed = closed_candles({"X": {"5m": [row(i * FIVE) for i in range(50)]}}, now, keep=20)
    assert len(closed["X"]["5m"]) == 20 and int(closed["X"]["5m"][-1][0]) == 49 * FIVE


def test_rows_without_time_and_unknown_timeframes_are_untouched():
    market = {"X": {"15m": ["candle"], "2h": [row(0), row(10 ** 15)]}}
    assert closed_candles(market, 1) == market


def test_decisions_get_closed_candles_and_spikes_and_prices_get_raw(cycle, monkeypatch):  # noqa: F811
    now_ms = int(time.time() * 1000)
    forming = now_ms - now_ms % FIVE
    raw = {"SOLUSDT": {"5m": [row(forming - 2 * FIVE, "100"), row(forming - FIVE, "101"), row(forming, "102.5")]}}
    cycle.candles = raw
    spikes, prices = [], []
    monkeypatch.setattr(analysis, "check_all_symbols_for_spikes", lambda symbols, candles: spikes.append(candles))
    monkeypatch.setattr(price_cache, "update", lambda symbol, price: prices.append((symbol, price)))
    assert analyse() is True
    (kwargs,) = cycle.generated
    assert [r[4] for r in kwargs["all_candles"]["SOLUSDT"]["5m"]] == ["100", "101"], "генератору — только закрытые"
    assert spikes == [raw], "резкие движения — по сырым свечам"
    assert prices == [("SOLUSDT", 102.5)], "кэш цены — по незакрытой свече"


def test_the_loop_fetches_one_extra_bar_and_the_generator_leaves_the_price_cache_alone():
    loop = (ROOT / "loops" / "market_analysis.py").read_text(encoding="utf-8")
    assert "get_candles_parallel, symbols, TIMEFRAMES, DECISION_BARS + 1, 20" in loop
    assert analysis.DECISION_BARS == 120
    generator = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    assert "_pc.update(" not in generator and "price_cache" not in generator.replace("кэш цены", "")
