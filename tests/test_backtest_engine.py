"""
Тесты для BacktestEngine — Фаза 3.
"""
import pytest
from analytics.backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    OHLCVCandle,
)


def make_candles(prices: list, base_ts: str = "2026-01-01T00:00:00") -> list:
    """Создаёт список OHLCVCandle из списка close-цен."""
    candles = []
    for i, price in enumerate(prices):
        ts = f"2026-01-01T{i:02d}:00:00"
        candles.append(OHLCVCandle(
            timestamp=ts,
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1000.0,
        ))
    return candles


# ---------------------------------------------------------------------------
# BacktestConfig
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_defaults(self):
        cfg = BacktestConfig()
        assert cfg.initial_balance == 10_000.0
        assert cfg.risk_pct == 0.02
        assert cfg.slippage_pct == pytest.approx(0.0003)

    def test_custom_config(self):
        cfg = BacktestConfig(initial_balance=5_000, risk_pct=0.01)
        assert cfg.initial_balance == 5_000.0
        assert cfg.risk_pct == 0.01


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class TestBacktestEngine:
    def test_empty_signals_returns_empty_result(self):
        engine = BacktestEngine()
        result = engine.run(signals=[], ohlcv={})
        assert result.total_trades == 0
        assert result.equity_curve == []

    def test_missing_ohlcv_for_symbol_is_skipped(self):
        engine = BacktestEngine()
        signals = [{'symbol': 'BTCUSDT', 'side': 'LONG',
                    'entry': 100.0, 'stop': 95.0,
                    'timestamp': '2026-01-01T00:00:00'}]
        result = engine.run(signals=signals, ohlcv={})
        assert result.total_trades == 0

    def test_signal_without_stop_is_skipped(self):
        engine = BacktestEngine()
        candles = make_candles([100, 105, 110])
        signals = [{'symbol': 'BTCUSDT', 'side': 'LONG',
                    'entry': 100.0, 'stop': None,
                    'timestamp': '2026-01-01T00:00:00'}]
        result = engine.run(signals=signals, ohlcv={'BTCUSDT': candles})
        assert result.total_trades == 0

    def test_long_trade_tp_hit(self):
        """LONG сделка с достижением TP."""
        config = BacktestConfig(initial_balance=10_000, risk_pct=0.02, slippage_pct=0.0)
        engine = BacktestEngine(config)

        # Цены: сначала идут вверх к TP=120
        prices = [100, 102, 105, 115, 125]
        candles = make_candles(prices)

        signals = [{
            'symbol': 'BTCUSDT',
            'side': 'LONG',
            'entry': 100.0,
            'stop': 90.0,
            'target': 120.0,
            'timestamp': '2026-01-01T00:00:00',
        }]

        result = engine.run(signals=signals, ohlcv={'BTCUSDT': candles})
        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade.close_reason == 'TP'
        assert trade.net_pnl > 0

    def test_long_trade_sl_hit(self):
        """LONG сделка с ударом по SL."""
        config = BacktestConfig(initial_balance=10_000, risk_pct=0.02, slippage_pct=0.0)
        engine = BacktestEngine(config)

        prices = [100, 98, 95, 88, 90]  # low падает ниже SL=92
        candles = make_candles(prices)
        # low каждой свечи = price * 0.99, поэтому при price=88 low=87.12 < 90
        sl_price = 90.0
        candles[3] = OHLCVCandle(
            timestamp=candles[3].timestamp,
            open=95, high=95, low=85, close=88, volume=1000
        )

        signals = [{
            'symbol': 'BTCUSDT',
            'side': 'LONG',
            'entry': 100.0,
            'stop': sl_price,
            'target': 130.0,
            'timestamp': '2026-01-01T00:00:00',
        }]
        result = engine.run(signals=signals, ohlcv={'BTCUSDT': candles})
        assert result.total_trades == 1
        trade = result.trades[0]
        assert trade.close_reason == 'SL'
        assert trade.net_pnl < 0

    def test_commission_reduces_pnl(self):
        """Комиссия уменьшает net P&L."""
        config_no_fee = BacktestConfig(
            initial_balance=10_000, risk_pct=0.02,
            slippage_pct=0.0, taker_fee=0.0
        )
        config_with_fee = BacktestConfig(
            initial_balance=10_000, risk_pct=0.02,
            slippage_pct=0.0, taker_fee=0.00055
        )
        prices = [100, 102, 105, 115, 125]
        candles = make_candles(prices)
        signal = [{
            'symbol': 'BTCUSDT', 'side': 'LONG',
            'entry': 100.0, 'stop': 90.0, 'target': 120.0,
            'timestamp': '2026-01-01T00:00:00',
        }]

        result_no_fee = BacktestEngine(config_no_fee).run(signal, {'BTCUSDT': candles})
        result_with_fee = BacktestEngine(config_with_fee).run(signal, {'BTCUSDT': candles})

        pnl_no_fee = result_no_fee.trades[0].net_pnl
        pnl_with_fee = result_with_fee.trades[0].net_pnl
        assert pnl_no_fee > pnl_with_fee

    def test_slippage_reduces_pnl(self):
        """Slippage уменьшает net P&L для LONG."""
        config_no_slip = BacktestConfig(
            initial_balance=10_000, risk_pct=0.02,
            slippage_pct=0.0, taker_fee=0.0
        )
        config_with_slip = BacktestConfig(
            initial_balance=10_000, risk_pct=0.02,
            slippage_pct=0.001, taker_fee=0.0
        )
        prices = [100, 105, 110, 125]
        candles = make_candles(prices)
        signal = [{
            'symbol': 'BTCUSDT', 'side': 'LONG',
            'entry': 100.0, 'stop': 90.0, 'target': 120.0,
            'timestamp': '2026-01-01T00:00:00',
        }]

        r1 = BacktestEngine(config_no_slip).run(signal, {'BTCUSDT': candles})
        r2 = BacktestEngine(config_with_slip).run(signal, {'BTCUSDT': candles})
        assert r1.trades[0].net_pnl > r2.trades[0].net_pnl

    def test_summary_dict(self):
        """summary_dict содержит все ключи."""
        config = BacktestConfig(slippage_pct=0.0, taker_fee=0.0)
        prices = [100, 110, 120]
        candles = make_candles(prices)
        signal = [{'symbol': 'X', 'side': 'LONG',
                   'entry': 100.0, 'stop': 90.0, 'target': 120.0,
                   'timestamp': '2026-01-01T00:00:00'}]
        result = BacktestEngine(config).run(signal, {'X': candles})
        d = result.summary_dict()
        assert 'total_trades' in d
        assert 'win_rate' in d
        assert 'final_balance' in d
        assert 'return_pct' in d
