"""
PositionSizer: номинал из риска и расстояния до стопа (аудит 10.09.2026, M6).

final_risk описан в конфиге как «риск на сделку, % от баланса» — сколько теряем
при срабатывании стопа. Раньше номиналом становился сам риск: balance ×
final_risk / 100. При 100 $ это 0,1–3 $ — ниже минимального ордера биржи.

Риск на сделку фиксирован (RISK_PERCENT) и от содержимого базы не зависит.
"""
import pytest

from core.position_sizer import PositionSizer

BALANCE = 100.0


def _calc(portfolio, stop):
    return PositionSizer().calculate(
        confidence=1.0, entropy=0.0, portfolio_state=portfolio, symbol="ADAUSDT",
        balance=BALANCE, stop_distance_pct=stop,
    )


def test_notional_is_risk_divided_by_stop_distance(empty_portfolio):
    result = _calc(empty_portfolio, 0.02)
    risk_usd = BALANCE * result.final_risk / 100
    assert result.position_size_usd == pytest.approx(risk_usd / 0.02)


def test_loss_at_stop_equals_intended_risk(empty_portfolio):
    """Главное свойство: при срабатывании стопа теряем ровно заявленный риск."""
    result = _calc(empty_portfolio, 0.015)
    loss_at_stop = result.position_size_usd * 0.015
    assert loss_at_stop == pytest.approx(BALANCE * result.final_risk / 100)


def test_at_100_usd_notional_clears_the_exchange_minimum(empty_portfolio):
    """Минимальный ордер Bybit — 5 $. Прежний расчёт давал 0,1–3 $."""
    assert _calc(empty_portfolio, 0.02).position_size_usd >= 5.0


def test_wider_stop_means_smaller_position(empty_portfolio):
    tight = _calc(empty_portfolio, 0.01).position_size_usd
    wide = _calc(empty_portfolio, 0.04).position_size_usd
    assert wide < tight


@pytest.mark.parametrize("stop", [None, 0, -0.01])
def test_without_valid_stop_falls_back_to_previous_formula(empty_portfolio, stop):
    result = _calc(empty_portfolio, stop)
    assert result.position_size_usd == pytest.approx(BALANCE * result.final_risk / 100)
