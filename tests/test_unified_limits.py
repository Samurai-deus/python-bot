"""
Единая модель лимитов (11.09.2026).

Риск на сделку — ровно RISK_PERCENT капитала; лимиты портфеля — только у Risk Core
(6 позиций, 6 % открытого риска, 3 % на группу, номинал до 300 % капитала). Остальные
модули меряют экспозицию долей его предела. До этого:
- PositionSizer урезал риск Kelly по смешанной истории, уверенностью, энтропией и
  свободным номиналом — 0,10–0,29 % вместо 1 %;
- RiskExposureBrain считал перегрузкой номинал > 50 % баланса и > 4 коррелированных
  позиций, мета-мозг жёстко блокировал номинал > 80 % баланса, PortfolioBrain — > 100 %:
  при позициях по 1 % риска портфель «переполнялся» после 1–2 сделок.
"""
import pathlib

import pytest

import config
import core.risk_core as rc
from brains.meta_decision_brain import MetaDecisionBrain
from brains.risk_exposure_brain import RiskExposureBrain
from core.position_sizer import PositionSizer
from core.risk_core import (BehavioralCounters, CapitalSnapshot, ExposureSnapshot, PositionSnapshot, RiskCore,
                            SystemHealthFlags, TradingIntent, TradingPermission)
from execution.gatekeeper import _notional_budget_usd
from tests.conftest import MockPortfolioState

ROOT = pathlib.Path(__file__).resolve().parent.parent
EQUITY = 100.0
STOP = 0.03                 # стоп 3 % от входа
RISK_PCT = 0.99             # риск демо-профиля (1 %) с запасом capital.RISK_LIMIT_HEADROOM
SIZE = EQUITY * RISK_PCT / 100 / STOP   # 33 $ номинала на позицию


def position(symbol):
    return PositionSnapshot(symbol=symbol, side="SHORT", position_size_usd=SIZE, entry_price=100.0,
                            stop_price=100.0 * (1 + STOP), leverage=2.0)


def risk_core_verdict(open_count):
    positions = [position(f"C{i}USDT") for i in range(open_count)]
    exposure = ExposureSnapshot(open_positions=positions, total_exposure_usd=SIZE * open_count,
                                max_single_position_usd=SIZE if positions else 0.0, correlation_groups={})
    new = TradingIntent(symbol="NEWUSDT", side="SHORT", position_size_usd=SIZE, entry_price=100.0,
                        stop_price=100.0 * (1 + STOP))
    capital = CapitalSnapshot(current_balance_usd=EQUITY, initial_balance_usd=EQUITY, total_loss_usd=0.0,
                              loss_24h_usd=0.0, loss_7d_usd=0.0)
    quiet = BehavioralCounters(actions_last_hour=0, actions_last_24h=0, consecutive_losses=0)
    healthy = SystemHealthFlags(is_safe_mode=False, consecutive_errors=0, runtime_healthy=True,
                                critical_modules_available=True)
    permission, _, report = RiskCore(rc.config_from_settings()).evaluate(new, capital, exposure, quiet, healthy)
    return permission, report


# --- размер ------------------------------------------------------------------

@pytest.mark.parametrize("confidence, entropy", [(0.2, 0.9), (0.66, 0.77), (1.0, 0.0)])
def test_the_risk_per_trade_is_fixed_whatever_the_signal_confidence(confidence, entropy):
    result = PositionSizer().calculate(confidence=confidence, entropy=entropy,
                                       portfolio_state=MockPortfolioState(available_ratio=0.4),
                                       symbol="ADAUSDT", balance=EQUITY, stop_distance_pct=STOP)
    assert result.position_allowed and result.final_risk == pytest.approx(config.RISK_PERCENT)
    assert result.position_size_usd * STOP == pytest.approx(EQUITY * config.RISK_PERCENT / 100), \
        "при срабатывании стопа теряется ровно риск на сделку"


def test_no_free_notional_means_no_position():
    result = PositionSizer().calculate(confidence=1.0, entropy=0.0, portfolio_state=MockPortfolioState(
        available_ratio=0.0), symbol="ADAUSDT", balance=EQUITY, stop_distance_pct=STOP)
    assert not result.position_allowed


def test_the_gatekeeper_sizes_from_equity_not_from_free_capital():
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    sizer = source[source.index("def _calculate_position_size"):source.index("self.position_sizer.calculate(")]
    assert "balance = get_current_balance()" in sizer and "balance = get_available_capital()" not in sizer


def test_kelly_from_the_mixed_history_is_gone():
    assert "kelly_fraction" not in (ROOT / "core" / "position_sizer.py").read_text(encoding="utf-8")
    assert "def kelly_fraction" not in (ROOT / "capital.py").read_text(encoding="utf-8")


# --- экспозиция — доля предела Risk Core --------------------------------------

def test_the_notional_budget_is_the_risk_core_limit():
    assert _notional_budget_usd(EQUITY) == pytest.approx(EQUITY * config.RISK_MAX_AGGREGATE_EXPOSURE_PCT / 100)
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert "risk_budget = current_balance" not in source and source.count("_notional_budget_usd(") == 5


def test_five_correlated_one_percent_positions_are_not_an_overload():
    assert not RiskExposureBrain()._check_overload(total_risk_pct=5 * RISK_PCT, exposure_pct=5 * SIZE / EQUITY * 100,
                                                   max_correlation=0.95, active_positions=5)


@pytest.mark.parametrize("risk, exposure, positions", [
    (config.RISK_MAX_OPEN_RISK_PCT + 0.5, 100.0, 5),
    (5.0, config.RISK_MAX_AGGREGATE_EXPOSURE_PCT + 10, 5),
    (5.0, 100.0, config.RISK_MAX_OPEN_POSITIONS + 1),
])
def test_an_overload_is_beyond_a_risk_core_limit(risk, exposure, positions):
    assert RiskExposureBrain()._check_overload(total_risk_pct=risk, exposure_pct=exposure,
                                               max_correlation=0.1, active_positions=positions)


def test_exposure_alone_is_no_hard_block_for_the_meta_brain():
    result = MetaDecisionBrain().evaluate(portfolio_exposure=0.95, confidence_score=0.8, entropy_score=0.2)
    assert result.allow_trading, result.reason


def test_a_loaded_portfolio_with_an_uncertain_signal_is_still_held_back():
    result = MetaDecisionBrain().evaluate(portfolio_exposure=0.7, confidence_score=0.45, entropy_score=0.55)
    assert not result.allow_trading


# --- сценарий: шесть позиций по 1 % проходят, седьмую держит Risk Core --------

def test_six_one_percent_positions_fit_and_the_seventh_is_refused_by_the_risk_core():
    for open_count in range(config.RISK_MAX_OPEN_POSITIONS):
        permission, report = risk_core_verdict(open_count)
        assert permission == TradingPermission.ALLOW, (open_count, report.violations)
        assert not RiskExposureBrain()._check_overload(open_count * RISK_PCT, open_count * SIZE / EQUITY * 100,
                                                       0.95, open_count)
        meta = MetaDecisionBrain().evaluate(portfolio_exposure=open_count * SIZE / _notional_budget_usd(EQUITY),
                                            confidence_score=0.7, entropy_score=0.3)
        assert meta.allow_trading, (open_count, meta.reason)
    permission, report = risk_core_verdict(config.RISK_MAX_OPEN_POSITIONS)
    assert permission == TradingPermission.DENY and "PORTFOLIO_OPEN_POSITIONS" in report.violated_invariants


# --- когнитивный фильтр --------------------------------------------------------

def test_the_cognitive_filter_counts_current_mode_trades_by_iso_time():
    source = (ROOT / "brains" / "cognitive_filter.py").read_text(encoding="utf-8")
    assert "WHERE created_at >" not in source and source.count("_current_mode_sql()") == 3, "объявление и два запроса"


def test_the_cognitive_hourly_limit_is_the_risk_core_one():
    from brains.cognitive_filter import CognitiveFilter
    assert CognitiveFilter().max_trades_per_hour == config.RISK_MAX_ACTIONS_PER_HOUR


@pytest.fixture
def db(tmp_path, monkeypatch):
    import database
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "limits.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def test_the_cognitive_filter_sees_todays_trades_of_the_current_mode(db, monkeypatch):
    """created_at в SQLite — «2026-09-11 16:28:35»: сделка того же дня раньше не засчитывалась."""
    import trading_mode
    from brains.cognitive_filter import CognitiveFilter
    monkeypatch.setattr(trading_mode, "sends_real_orders", lambda: True)
    db.add_trade("ADAUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0, exchange_order_id="order-1")
    db.add_trade("SOLUSDT", "LONG", 1.0, 0.9, 1.1, position_size=10.0)  # бумажная — другой режим
    trades = CognitiveFilter()._get_recent_trades_from_db(hours=1)
    assert [t["symbol"] for t in trades] == ["ADAUSDT"]
