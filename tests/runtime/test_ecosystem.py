"""
Санити-проверки «мозгов» экосистемы (задача 1.6).

Перенесено из корневого test_ecosystem.py: там каждая проверка была завёрнута
в except Exception и под pytest проходила при любом исходе. Проверки
DecisionCore (перегрузка, пауза) не перенесены — они есть в
tests/test_decision_core.py. Проверка гейткипера тоже: она меняла состояние
синглтона DecisionCore для всех следующих тестов. Здесь — свежие экземпляры,
а мозгам, читающим журнал сделок, — временная база.
"""
import pytest

import database
from brains.cognitive_filter import CognitiveFilter
from brains.market_regime_brain import MarketRegimeBrain
from brains.risk_exposure_brain import RiskExposureBrain

BTC_CANDLE = [0, 50000, 51000, 49000, 50500, 1000]
ETH_CANDLE = [0, 3000, 3100, 2900, 3050, 500]


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "ecosystem.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def test_market_regime_brain_returns_known_values():
    candles = {
        "BTCUSDT": {tf: [BTC_CANDLE] * 20 for tf in ("15m", "30m", "4h")},
        "ETHUSDT": {tf: [ETH_CANDLE] * 20 for tf in ("15m", "30m", "4h")},
    }
    regime = MarketRegimeBrain().analyze(["BTCUSDT", "ETHUSDT"], candles)
    assert regime.trend_type in ("TREND", "RANGE", "MIXED")
    assert regime.volatility_level in ("HIGH", "MEDIUM", "LOW")


def test_risk_exposure_brain_without_positions(db):
    risk = RiskExposureBrain().analyze(["BTCUSDT"], {"BTCUSDT": {"15m": [BTC_CANDLE] * 20}})
    assert risk.active_positions == 0
    assert risk.total_risk_pct == 0
    assert not risk.is_overloaded


def test_cognitive_filter_on_empty_journal(db):
    cognitive = CognitiveFilter().analyze()
    assert 0.0 <= cognitive.overtrading_score <= 1.0
    assert cognitive.recent_trades_count == 0
    assert cognitive.should_pause is False
