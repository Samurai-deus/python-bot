"""
Тесты для финансовых метрик (bot_statistics.py) — Фаза 3.
"""
import pytest
from bot_statistics import (
    calculate_sharpe_ratio,
    calculate_max_drawdown,
    calculate_profit_factor,
    calculate_recovery_factor,
    calculate_expectancy,
)


# ---------------------------------------------------------------------------
# calculate_sharpe_ratio
# ---------------------------------------------------------------------------

class TestSharpeRatio:
    def test_returns_none_for_empty(self):
        assert calculate_sharpe_ratio([]) is None

    def test_returns_none_for_single_point(self):
        assert calculate_sharpe_ratio([10_000.0]) is None

    def test_positive_sharpe_for_growing_curve(self):
        # Равномерный рост → стабильные доходности → положительный Sharpe
        curve = [10_000 + i * 100 for i in range(30)]  # +1% каждый шаг
        sharpe = calculate_sharpe_ratio(curve)
        assert sharpe is not None
        assert sharpe > 0

    def test_negative_sharpe_for_declining_curve(self):
        # Равномерное падение
        curve = [10_000 - i * 100 for i in range(30)]
        sharpe = calculate_sharpe_ratio(curve)
        assert sharpe is not None
        assert sharpe < 0

    def test_returns_none_for_flat_curve(self):
        # Нулевое std → None
        curve = [10_000.0] * 20
        assert calculate_sharpe_ratio(curve) is None

    def test_risk_free_reduces_sharpe(self):
        curve = [10_000 + i * 50 for i in range(30)]
        sharpe_no_rf = calculate_sharpe_ratio(curve, risk_free=0.0)
        sharpe_with_rf = calculate_sharpe_ratio(curve, risk_free=0.05)
        assert sharpe_no_rf is not None
        assert sharpe_with_rf is not None
        assert sharpe_no_rf > sharpe_with_rf


# ---------------------------------------------------------------------------
# calculate_max_drawdown
# ---------------------------------------------------------------------------

class TestMaxDrawdown:
    def test_no_drawdown(self):
        curve = [10_000, 11_000, 12_000, 13_000]
        dd_abs, dd_pct = calculate_max_drawdown(curve)
        assert dd_abs == 0.0
        assert dd_pct == 0.0

    def test_empty_curve(self):
        dd_abs, dd_pct = calculate_max_drawdown([])
        assert dd_abs == 0.0
        assert dd_pct == 0.0

    def test_single_drawdown(self):
        # peak=12000, trough=9000 → dd=3000, pct=0.25
        curve = [10_000, 12_000, 9_000, 11_000]
        dd_abs, dd_pct = calculate_max_drawdown(curve)
        assert dd_abs == pytest.approx(3_000, abs=0.01)
        assert dd_pct == pytest.approx(0.25, abs=0.001)

    def test_full_loss(self):
        curve = [10_000, 5_000, 1_000]
        dd_abs, dd_pct = calculate_max_drawdown(curve)
        assert dd_abs == pytest.approx(9_000, abs=0.01)
        assert dd_pct == pytest.approx(0.9, abs=0.001)


# ---------------------------------------------------------------------------
# calculate_profit_factor
# ---------------------------------------------------------------------------

class TestProfitFactor:
    def test_none_when_no_losses(self):
        trades = [{'pnl': 100}, {'pnl': 200}, {'pnl': 50}]
        assert calculate_profit_factor(trades) is None

    def test_basic_profit_factor(self):
        # gross_profit=300, gross_loss=100 → PF=3.0
        trades = [{'pnl': 100}, {'pnl': 200}, {'pnl': -100}]
        pf = calculate_profit_factor(trades)
        assert pf == pytest.approx(3.0, abs=0.001)

    def test_pf_less_than_1_when_more_losses(self):
        trades = [{'pnl': 50}, {'pnl': -100}, {'pnl': -200}]
        pf = calculate_profit_factor(trades)
        assert pf is not None
        assert pf < 1.0

    def test_empty_trades(self):
        # gross_loss=0 → None
        assert calculate_profit_factor([]) is None


# ---------------------------------------------------------------------------
# calculate_recovery_factor
# ---------------------------------------------------------------------------

class TestRecoveryFactor:
    def test_none_when_no_drawdown(self):
        trades = [{'pnl': 100}, {'pnl': 200}]
        curve = [10_000, 11_000, 12_000]
        assert calculate_recovery_factor(trades, curve) is None

    def test_basic_recovery_factor(self):
        trades = [{'pnl': 200}, {'pnl': -50}, {'pnl': 100}]  # net=+250
        curve = [10_000, 11_000, 9_000, 10_500]  # max_dd_abs=2000
        rf = calculate_recovery_factor(trades, curve)
        assert rf is not None
        # net=250, dd_abs=2000 → rf=0.125
        assert rf == pytest.approx(250 / 2000, abs=0.001)


# ---------------------------------------------------------------------------
# calculate_expectancy
# ---------------------------------------------------------------------------

class TestExpectancy:
    def test_none_for_empty(self):
        assert calculate_expectancy([]) is None

    def test_positive_expectancy(self):
        # 2 wins (+100 avg), 1 loss (-50) → wr=0.667, lr=0.333
        # exp = 0.667*100 - 0.333*50 ≈ 50.0
        trades = [{'pnl': 100}, {'pnl': 100}, {'pnl': -50}]
        exp = calculate_expectancy(trades)
        assert exp is not None
        assert exp > 0

    def test_negative_expectancy(self):
        trades = [{'pnl': -100}, {'pnl': -100}, {'pnl': 20}]
        exp = calculate_expectancy(trades)
        assert exp is not None
        assert exp < 0

    def test_all_wins(self):
        trades = [{'pnl': 50}, {'pnl': 100}]
        exp = calculate_expectancy(trades)
        assert exp is not None
        assert exp > 0
