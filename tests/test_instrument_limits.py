"""
Лимиты биржи на размер ордера и итоговый размер позиции (аудит 10.09.2026: M6,
реалистичность бумажной торговли на 100 $).

Лимиты — реальные значения Bybit на 10.09.2026; биржа подменена функцией, сеть
не нужна.
"""
from decimal import Decimal
from pathlib import Path

import pytest

from execution.sizing_guard import finalize_position_size, unaffordable_reason
from market_data import instrument_limits as il

BTC = {"min_qty": Decimal("0.001"), "qty_step": Decimal("0.001"), "min_notional": Decimal("5"), "status": "Trading"}
ADA = {"min_qty": Decimal("1"), "qty_step": Decimal("1"), "min_notional": Decimal("5"), "status": "Trading"}
BTC_PRICE = 78110.40
ADA_PRICE = 0.2136


@pytest.fixture(autouse=True)
def fresh_cache():
    il.clear_cache()
    yield
    il.clear_cache()


def fixed(limits):
    return lambda symbol: limits


# ---------------------------------------------------------------------------
# Минимальный ордер
# ---------------------------------------------------------------------------

def test_btc_order_below_one_lot_is_rejected():
    """0,001 BTC ≈ 78 $ — меньше биржа не примет."""
    reason = il.min_order_violation("BTCUSDT", 50.0, BTC_PRICE, fetch=fixed(BTC))
    assert reason and "ниже минимального ордера" in reason and "78.11" in reason


def test_btc_order_of_one_lot_is_accepted():
    assert il.min_order_violation("BTCUSDT", 80.0, BTC_PRICE, fetch=fixed(BTC)) is None


def test_rounding_down_to_lot_step_can_drop_below_minimum():
    """
    78,50 $ — это 0,001005 BTC, после округления вниз 0,001 = 78,11 $. Проходит.
    А 78,00 $ — это 0,000998 BTC, округление вниз даёт 0. Биржа не примет.
    """
    assert il.min_order_violation("BTCUSDT", 78.50, BTC_PRICE, fetch=fixed(BTC)) is None
    assert il.min_order_violation("BTCUSDT", 78.00, BTC_PRICE, fetch=fixed(BTC)) is not None


def test_min_notional_binds_for_cheap_coins():
    """У дешёвых монет ограничивает не лот, а минимальный номинал 5 $."""
    assert il.min_order_violation("ADAUSDT", 4.0, ADA_PRICE, fetch=fixed(ADA)) is not None
    assert il.min_order_violation("ADAUSDT", 6.0, ADA_PRICE, fetch=fixed(ADA)) is None


@pytest.mark.parametrize("notional", [0.1, 1.0, 3.0])
def test_old_sizing_at_100_usd_is_unplaceable_everywhere(notional):
    """
    Прежний расчёт при балансе 100 $ давал 0,1–3 $. Такой ордер не примет ни
    одна пара: минимум — 5 $. Этот тест фиксирует, почему бумажные результаты
    на старом расчёте ничего не говорили о реальной торговле.
    """
    assert il.min_order_violation("BTCUSDT", notional, BTC_PRICE, fetch=fixed(BTC)) is not None
    assert il.min_order_violation("ADAUSDT", notional, ADA_PRICE, fetch=fixed(ADA)) is not None


def test_non_trading_instrument_is_rejected():
    halted = dict(BTC, status="Settling")
    reason = il.min_order_violation("BTCUSDT", 100.0, BTC_PRICE, fetch=fixed(halted))
    assert reason and "не торгуется" in reason


@pytest.mark.parametrize("price", [None, 0, -1])
def test_missing_entry_price_is_rejected(price):
    assert il.min_order_violation("BTCUSDT", 100.0, price, fetch=fixed(BTC)) is not None


# ---------------------------------------------------------------------------
# Кэш и отказ биржи
# ---------------------------------------------------------------------------

def test_unknown_limits_reject_the_order():
    """Без лимитов нельзя сказать, примет ли биржа ордер, — значит, не открываем."""
    reason = il.min_order_violation("BTCUSDT", 100.0, BTC_PRICE, fetch=lambda s: None)
    assert reason and "недоступны" in reason


def test_exception_while_fetching_rejects_when_nothing_cached():
    def boom(symbol):
        raise ConnectionError("bybit down")

    assert il.min_order_violation("BTCUSDT", 100.0, BTC_PRICE, fetch=boom) is not None


def test_stale_cache_is_used_when_refresh_fails(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(il.time, "time", lambda: now[0])
    assert il.get_limits("BTCUSDT", fetch=fixed(BTC)) == BTC

    now[0] += il.CACHE_TTL_SECONDS + 1
    assert il.get_limits("BTCUSDT", fetch=lambda s: None) == BTC


def test_fresh_cache_does_not_refetch():
    calls = []

    def counting(symbol):
        calls.append(symbol)
        return BTC

    il.get_limits("BTCUSDT", fetch=counting)
    il.get_limits("BTCUSDT", fetch=counting)
    assert calls == ["BTCUSDT"]


# ---------------------------------------------------------------------------
# Итоговый размер позиции
# ---------------------------------------------------------------------------

def test_final_size_never_exceeds_what_risk_core_approved():
    """M6: Risk Core одобряет верхнюю границу; дальше размер может только уменьшаться."""
    size, reason = finalize_position_size("ADAUSDT", 150.0, 60.0, ADA_PRICE, fetch=fixed(ADA))
    assert reason is None
    assert size == 60.0


def test_smaller_sized_value_is_kept():
    size, reason = finalize_position_size("ADAUSDT", 40.0, 60.0, ADA_PRICE, fetch=fixed(ADA))
    assert reason is None and size == 40.0


def test_without_prior_approval_sized_value_is_used():
    size, reason = finalize_position_size("ADAUSDT", 40.0, 0.0, ADA_PRICE, fetch=fixed(ADA))
    assert reason is None and size == 40.0


def test_capped_size_below_exchange_minimum_is_blocked():
    """Урезание до одобренного может увести размер ниже минимума биржи — тогда отказ."""
    size, reason = finalize_position_size("BTCUSDT", 150.0, 50.0, BTC_PRICE, fetch=fixed(BTC))
    assert size is None
    assert "ниже минимального ордера" in reason


@pytest.mark.parametrize("sized", [None, 0, -5])
def test_no_positive_size_is_blocked(sized):
    size, reason = finalize_position_size("ADAUSDT", sized, 60.0, ADA_PRICE, fetch=fixed(ADA))
    assert size is None and reason


# ---------------------------------------------------------------------------
# Символ, который не открыть ни при каком сигнале
# ---------------------------------------------------------------------------

SOL = {"min_qty": Decimal("0.1"), "qty_step": Decimal("0.1"), "min_notional": Decimal("5"), "status": "Trading"}


def test_btc_is_not_a_candidate_at_100_usd():
    """Предел позиции 10 $ (10 % от 100 $), минимальный ордер BTC около 78 $."""
    reason = unaffordable_reason("BTCUSDT", BTC_PRICE, 10.0, fetch=fixed(BTC))
    assert reason and "78.11" in reason


def test_sol_just_above_the_cap_is_not_a_candidate():
    assert unaffordable_reason("SOLUSDT", 100.12, 10.0, fetch=fixed(SOL)) is not None


def test_cheap_coin_and_bigger_capital_are_candidates():
    assert unaffordable_reason("ADAUSDT", ADA_PRICE, 10.0, fetch=fixed(ADA)) is None
    assert unaffordable_reason("BTCUSDT", BTC_PRICE, 100.0, fetch=fixed(BTC)) is None, "при капитале 1000 $"


def test_unknown_limits_price_or_cap_do_not_skip():
    """Без данных символ не отсеивается — решают проверки дальше по цепочке."""
    assert unaffordable_reason("BTCUSDT", BTC_PRICE, 10.0, fetch=lambda symbol: None) is None
    assert unaffordable_reason("BTCUSDT", None, 10.0, fetch=fixed(BTC)) is None
    assert unaffordable_reason("BTCUSDT", BTC_PRICE, 0.0, fetch=fixed(BTC)) is None


def test_signal_generator_skips_unaffordable_symbols():
    text = (Path(__file__).resolve().parent.parent / "signal_generator.py").read_text(encoding="utf-8")
    assert "unaffordable_reason(symbol" in text
