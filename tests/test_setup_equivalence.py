"""
Эталон генератора сигналов (Ф1 плана трейдера, шаг 2): что генератор отдаёт гейткиперу
и пишет в журнал на синтетическом рынке.

Эталон снят с кода ДО выноса оценки сетапа в чистую функцию (tests/fixtures/setup_golden.json).
Вынос не должен менять ни одного решения: тот же символ, сторона, вход, стоп, цель,
стратегия, риск, score и режим. Меняет решения только осознанная правка — тогда эталон
пересоздаётся (py tests/test_setup_equivalence.py --write) и разница объясняется в PR.

Побочные эффекты генератора подменены: гейткипер записывает и отклоняет, журнал
записывает, размер позиции фиксирован, API фандинга и открытого интереса пусты.
"""
import json
import math
import pathlib
import random
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN = ROOT / "tests" / "fixtures" / "setup_golden.json"
# Режим рынка из SystemState по очереди: без него, тренд, диапазон и неоднозначный (MIXED
# генератор трактует как RANGE). Без режимов эталон не видел логику выбора режима: мутации
# «по умолчанию TREND» и «режим из ADX не учитывается» выживали.
REGIMES = (None, "TREND", "RANGE", "MIXED")
STEP_MS = 300_000
T0 = 1_725_000_000_000
PER_TF = {"5m": 1, "15m": 3, "30m": 6, "1h": 12, "4h": 48}
BARS = 120
# (сдвиг цены за 5m, волатильность) — тренды вверх/вниз, флэт, разная волатильность
SCENARIOS = ([(d, v) for d in (-0.0025, -0.0008, 0.0, 0.0008, 0.0025) for v in (0.0015, 0.004)]
             + [(0.0, 0.012), (0.004, 0.009), (0.0003, 0.0006), (-0.0003, 0.0006), (0.0015, 0.0025), (-0.0015, 0.0025),
                (0.0, 0.0008), (0.006, 0.003), (-0.006, 0.003), (0.0001, 0.02)])


def _five_minute_series(rng, drift, vol, count):
    price, rows = 100.0 * (0.5 + rng.random()), []
    for i in range(count):
        # режим внутри ряда меняется: последние 20 % — разворот, чтобы стратегии видели откаты
        d = drift if i < count * 0.8 else -drift * 0.7
        o = price
        c = o * math.exp(d + vol * rng.gauss(0, 1))
        h = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
        low = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
        volume = 1000 * (1 + abs(rng.gauss(0, 0.6)))
        rows.append([T0 + i * STEP_MS, o, h, low, c, volume])
        price = c
    return rows


def _aggregate(rows, n):
    out = []
    usable = len(rows) - len(rows) % n
    for i in range(len(rows) - usable, len(rows), n):
        chunk = rows[i:i + n]
        out.append([str(chunk[0][0]), str(chunk[0][1]), str(max(r[2] for r in chunk)), str(min(r[3] for r in chunk)),
                    str(chunk[-1][4]), str(sum(r[5] for r in chunk)), str(sum(r[5] * r[4] for r in chunk))])
    return out[-BARS:]


def synthetic_market(seed, drift, vol, symbols):
    market = {}
    for k, symbol in enumerate(symbols):
        rng = random.Random(seed * 1000 + k)
        base = _five_minute_series(rng, drift * (1 + 0.3 * rng.gauss(0, 1)), vol, BARS * PER_TF["4h"])
        market[symbol] = {tf: _aggregate(base, n) for tf, n in PER_TF.items()}
    return market


def _num(x):
    return round(float(x), 10) if isinstance(x, (int, float)) and not isinstance(x, bool) else x


def run_scenarios(monkeypatch, markets=None, symbols=None):
    """Прогон генератора по всем сценариям с подменёнными побочными эффектами."""
    import signal_generator as sg
    import price_cache
    import journal
    from ai_trader import worker
    from brains import trade_learner
    from market_data import bybit_market_data

    events = []

    class Gatekeeper:
        last_block_reason = ("stub", "stub")

        def send_signal(self, symbol, signal_data, **kwargs):
            events.append(["GK", symbol, signal_data["side"], _num(signal_data["entry"]), _num(signal_data["stop"]),
                           _num(signal_data["target"]), signal_data["strategy_name"], signal_data["risk"],
                           _num(signal_data["score"]), signal_data["mode"], _num(signal_data["rr_ratio"]),
                           _num(signal_data["leverage"])])
            return False

    def record_signal(**f):
        events.append(["J", f["status"], f.get("reason_code"), f["symbol"], f.get("side"), _num(f.get("entry")),
                       _num(f.get("stop")), _num(f.get("target")), f.get("strategy")])
        return True

    state = SimpleNamespace(market_regime=None, would_be_new_signal=lambda *a: True,
                            is_new_signal=lambda *a: True)
    monkeypatch.setattr(sg, "position_size", lambda entry, stop, side: 50.0)
    monkeypatch.setattr(sg, "unaffordable_reason", lambda *a, **k: None)
    monkeypatch.setattr(sg, "log_monitor", lambda *a, **k: None)
    monkeypatch.setattr(price_cache, "update", lambda *a, **k: None)
    monkeypatch.setattr(journal, "record_signal", record_signal)
    monkeypatch.setattr(journal, "log_signal_snapshot", lambda *a, **k: True)
    monkeypatch.setattr(worker, "submit", lambda *a, **k: False)
    monkeypatch.setattr(trade_learner, "should_skip_symbol", lambda s: False)
    monkeypatch.setattr(trade_learner, "get_symbol_adjustment", lambda s, side: 0.0)
    monkeypatch.setattr(trade_learner, "get_confidence_calibration", lambda c: 0.0)
    monkeypatch.setattr(bybit_market_data, "get_open_interest", lambda *a, **k: [])
    monkeypatch.setattr(bybit_market_data, "get_funding_rate", lambda *a, **k: [])

    if symbols is not None:
        monkeypatch.setattr(sg, "SYMBOLS", list(symbols))
    synthetic = markets is None
    if synthetic:
        markets = ((seed, synthetic_market(seed, drift, vol, sg.SYMBOLS)) for seed, (drift, vol) in enumerate(SCENARIOS))
    from core.decision_core import MarketRegime
    for seed, market in markets:
        trend = REGIMES[seed % len(REGIMES)] if synthetic else None
        state.market_regime = (MarketRegime(trend_type=trend, volatility_level="MEDIUM", risk_sentiment="NEUTRAL",
                                            confidence=0.6) if trend else None)
        events.append(["SCENARIO", seed])
        sg.generate_signals_for_symbols(market, {}, True, decision_core=SimpleNamespace(),
                                        opportunity_awareness=SimpleNamespace(analyze=lambda *a: None),
                                        gatekeeper=Gatekeeper(), system_state=state)
    return events


def _same(a, b):
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, (int, float)) and isinstance(b, (int, float)) and math.isclose(a, b, rel_tol=1e-7, abs_tol=1e-9)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def test_the_generator_decides_as_in_the_golden_file(monkeypatch):
    events = run_scenarios(monkeypatch)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(events) == len(golden), f"событий {len(events)}, в эталоне {len(golden)}"
    diffs = [(i, g, e) for i, (g, e) in enumerate(zip(golden, events)) if not _same(g, e)]
    assert not diffs, f"расхождений {len(diffs)}, первое: {diffs[0]}"


def test_the_golden_file_covers_every_path():
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    kinds = {e[0] for e in golden}
    journal_codes = {e[2] for e in golden if e[0] == "J"}
    strategies = {e[6] for e in golden if e[0] == "GK"}
    assert {"GK", "J"} <= kinds
    assert "legacy" in strategies and len(strategies) >= 2, strategies
    assert journal_codes, "эталон без отсева — ветки high_risk/low_rr не проверены"
    assert len(REGIMES) == 4 and len(SCENARIOS) >= 2 * len(REGIMES), "каждый режим — хотя бы в двух сценариях"


def _candidates():
    """Символы эталона, дошедшие до гейткипера, — их свечи доходят до стратегий."""
    import signal_generator as sg
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    seed = None
    for e in golden:
        if e[0] == "SCENARIO":
            seed = e[1]
        elif e[0] == "GK":
            drift, vol = SCENARIOS[seed]
            yield e[1], synthetic_market(seed, drift, vol, sg.SYMBOLS)[e[1]]


@pytest.mark.parametrize("trend, adx_value, expected", [
    (None, 30.0, "TREND"),     # режима нет — берётся из ADX 15m
    (None, 10.0, "RANGE"),
    ("MIXED", 10.0, "RANGE"),  # неоднозначный — как диапазон
    ("RANGE", 30.0, "TREND"),  # диапазон по мозгу режимов, но ADX > 25 — тренд
    ("TREND", 10.0, "TREND"),
])
def test_the_regime_handed_to_the_strategies(monkeypatch, trend, adx_value, expected):
    """
    Режим рынка почти не меняет решения: trend_following применима и в TREND, и в RANGE,
    остальные стратегии режим не смотрят, режим — лишь множитель при выборе между
    сигналами. Поэтому эталон его не видит, и логика выбора режима проверяется напрямую:
    какой режим evaluate_setup передаёт менеджеру стратегий.
    """
    from core.decision_core import MarketRegime
    from strategies import setup
    real_adx = setup.adx
    monkeypatch.setattr(setup, "adx", lambda candles, period=14: {**real_adx(candles, period=period), "adx": adx_value})
    regime = MarketRegime(trend_type=trend, volatility_level="MEDIUM", risk_sentiment="NEUTRAL") if trend else None
    for symbol, market in _candidates():
        seen = []

        class Manager:
            def get_best_signal(self, *args, market_regime=None, volatility_level=None, **kwargs):
                seen.append(market_regime)
                return None

        setup.evaluate_setup(symbol, market, market_correlations={}, good_time=True, market_regime=regime,
                             strategy_manager=Manager())
        if seen:
            assert seen == [expected]
            return
    raise AssertionError("ни один символ эталона не дошёл до стратегий")


if __name__ == "__main__" and "--write" in sys.argv:
    import pytest
    mp = pytest.MonkeyPatch()
    try:
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(run_scenarios(mp), ensure_ascii=False, indent=0), encoding="utf-8")
    finally:
        mp.undo()
    print("golden written:", GOLDEN)
