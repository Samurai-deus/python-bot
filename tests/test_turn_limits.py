"""
Урезание размера не ниже минимума биржи и до 3 новых позиций за оборот
(шаг 2б плана обучения на демо-счёте, 11.09.2026).

До этого любое «урезать» на счёте в 100 $ было «отказать»: половина позиции в
10 $ — меньше минимального ордера, и сигнал отсекался. А пауза 60 с между
действиями Risk Core вместе с урезанием вдвое оставляла одну сделку за оборот.
Лимиты Bybit — реальные значения; биржа подменена функцией, сеть не нужна.
"""
import pathlib
from decimal import Decimal

import pytest

import core.risk_core as rc
from execution.sizing_guard import finalize_position_size, reduce_keeping_minimum
from market_data import instrument_limits as il

ROOT = pathlib.Path(__file__).resolve().parent.parent
ADA = {"min_qty": Decimal("1"), "qty_step": Decimal("1"), "min_notional": Decimal("5"), "status": "Trading"}
BTC = {"min_qty": Decimal("0.001"), "qty_step": Decimal("0.001"), "min_notional": Decimal("5"), "status": "Trading"}
ADA_PRICE = 0.2136
BTC_PRICE = 78110.40


def fixed(limits):
    return lambda symbol: limits


@pytest.fixture(autouse=True)
def fresh_cache():
    il.clear_cache()
    yield
    il.clear_cache()


def test_the_smallest_order_survives_rounding_down():
    """5 $ по ADA — 23 монеты на 4,91 $ (ниже минимума); наименьший проходящий — 24 монеты."""
    floor = il.smallest_order_usd("ADAUSDT", ADA_PRICE, fetch=fixed(ADA))
    assert floor == pytest.approx(24 * ADA_PRICE)
    assert il.min_order_violation("ADAUSDT", 5.0, ADA_PRICE, fetch=fixed(ADA)) is not None
    assert il.min_order_violation("ADAUSDT", floor, ADA_PRICE, fetch=fixed(ADA)) is None


def test_the_smallest_order_follows_the_minimum_quantity():
    assert il.smallest_order_usd("BTCUSDT", BTC_PRICE, fetch=fixed(BTC)) == pytest.approx(0.001 * BTC_PRICE)


def test_no_price_or_limits_means_no_floor():
    assert il.smallest_order_usd("ADAUSDT", 0.0, fetch=fixed(ADA)) is None
    assert il.smallest_order_usd("ADAUSDT", ADA_PRICE, fetch=lambda s: None) is None


@pytest.mark.parametrize("size, factor, expected", [
    (10.0, 0.5, 24 * ADA_PRICE),   # половина 10 $ — ниже минимума: до наименьшего ордера
    (40.0, 0.5, 20.0),             # половина 40 $ — выше минимума: как есть
    (4.0, 0.5, 2.0),               # исходный размер сам меньше минимума — не раздуваем
])
def test_a_reduction_never_goes_below_the_exchange_minimum(size, factor, expected):
    assert reduce_keeping_minimum("ADAUSDT", size, factor, ADA_PRICE, fetch=fixed(ADA)) == pytest.approx(expected)


def test_a_reduction_without_limits_is_plain():
    assert reduce_keeping_minimum("ADAUSDT", 10.0, 0.5, ADA_PRICE, fetch=lambda s: None) == pytest.approx(5.0)


def test_a_small_sized_value_is_raised_to_the_minimum_within_the_approval():
    size, reason = finalize_position_size("ADAUSDT", 3.0, 60.0, ADA_PRICE, fetch=fixed(ADA))
    assert reason is None and size == pytest.approx(24 * ADA_PRICE)


@pytest.mark.parametrize("approved", [4.0, 0.0, None])
def test_no_raise_above_the_approval_or_without_one(approved):
    size, reason = finalize_position_size("ADAUSDT", 3.0, approved, ADA_PRICE, fetch=fixed(ADA))
    assert size is None and "ниже минимального ордера" in reason


def test_the_gatekeeper_reductions_keep_the_minimum():
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert source.count("reduce_keeping_minimum(") == 2, "ALLOW_LIMITED и PortfolioBrain"
    assert "original_size * 0.5" not in source
    assert "original_size * portfolio_analysis.recommended_size_multiplier" not in source


def test_the_behavioral_limits_by_default():
    limits = rc.config_from_settings()
    assert limits.action_cooldown_seconds == 0, "вместо паузы — предел новых позиций за оборот"
    assert (limits.max_actions_per_hour, limits.max_actions_per_24h) == (12, 100)


def test_the_adr_behavioral_defaults_stay_for_the_invariant_tests():
    limits = rc.RiskCoreConfig()
    assert (limits.action_cooldown_seconds, limits.max_actions_per_hour, limits.max_actions_per_24h) == (60, 10, 50)


def test_the_generator_sends_at_most_n_new_positions_per_turn():
    import config
    assert config.MAX_NEW_POSITIONS_PER_TURN == 3
    source = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    cap = source.index('if stats["signals_sent"] >= MAX_NEW_POSITIONS_PER_TURN:')
    assert cap < source.index("continue", cap) < source.index('"%s: sending signal via Gatekeeper"', cap)
