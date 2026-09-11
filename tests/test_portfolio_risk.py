"""
Портфель по риску (шаг 2 плана обучения на демо-счёте, 11.09.2026).

Прежние пределы Risk Core — 10 % номинала на позицию, 50 % на все, 30 % на группу —
при счёте в 100 $ давали сделки по 10 $ и отказ почти каждому следующему сигналу.
Теперь размер задаёт риск на сделку, а портфель ограничен числом позиций и
суммарным риском; номинальные пределы — под позиции с плечом.
"""
import pathlib

import pytest

import config
import core.risk_core as rc
from core.risk_core import (BehavioralCounters, CapitalSnapshot, ExposureSnapshot, PositionSnapshot,
                            RiskCore, RiskCoreConfig, SystemHealthFlags, TradingIntent, TradingPermission)

ROOT = pathlib.Path(__file__).resolve().parent.parent

CAPITAL = CapitalSnapshot(current_balance_usd=100.0, initial_balance_usd=100.0, total_loss_usd=0.0,
                          loss_24h_usd=0.0, loss_7d_usd=0.0)
QUIET = BehavioralCounters(actions_last_hour=0, actions_last_24h=0, consecutive_losses=0)
HEALTHY = SystemHealthFlags(is_safe_mode=False, consecutive_errors=0, runtime_healthy=True,
                            critical_modules_available=True)


def position(symbol, side="SHORT", size=25.0, entry=100.0, stop=104.0):
    """По умолчанию риск 1 $: 25 $ номинала при стопе в 4 %."""
    return PositionSnapshot(symbol=symbol, side=side, position_size_usd=size, entry_price=entry,
                            stop_price=stop, leverage=5.0)


def intent(symbol="SOLUSDT", side="SHORT", size=50.0, entry=100.0, stop=102.0):
    """По умолчанию риск 1 $ (1 % от 100 $): 50 $ номинала при стопе в 2 %."""
    return TradingIntent(symbol=symbol, side=side, position_size_usd=size, entry_price=entry, stop_price=stop)


def exposure(*positions, groups=None):
    return ExposureSnapshot(
        open_positions=list(positions),
        total_exposure_usd=sum(p.position_size_usd for p in positions),
        max_single_position_usd=max([p.position_size_usd for p in positions], default=0.0),
        correlation_groups=groups or {},
    )


def evaluate(exp, trade=None):
    return RiskCore(rc.config_from_settings()).evaluate(trade or intent(), CAPITAL, exp, QUIET, HEALTHY)


def test_a_normal_leveraged_trade_is_allowed():
    """Риск 1 %, номинал 50 % капитала — раньше это было 5× выше предела одной позиции."""
    permission, _, report = evaluate(exposure())
    assert permission == TradingPermission.ALLOW, report.violations


def test_the_seventh_position_is_refused():
    open_six = [position(f"C{i}USDT", size=10.0) for i in range(6)]
    permission, _, report = evaluate(exposure(*open_six))
    assert permission == TradingPermission.DENY
    assert "PORTFOLIO_OPEN_POSITIONS" in report.violated_invariants


def test_total_open_risk_is_capped():
    five_at_one_dollar = [position(f"C{i}USDT") for i in range(5)]  # 5 $ риска уже открыто
    permission, _, report = evaluate(exposure(*five_at_one_dollar), intent(size=60.0))  # ещё 1,2 $ → 6,2 %
    assert permission == TradingPermission.DENY
    assert report.violated_invariants == ["PORTFOLIO_OPEN_RISK"], report.violations


def test_group_risk_counts_only_the_same_direction():
    groups = {"alt-l1": ["SOLUSDT", "AVAXUSDT", "NEARUSDT"]}
    open_in_group = exposure(position("AVAXUSDT", size=37.5), position("NEARUSDT"), groups=groups)  # 2,5 $ в SHORT
    short_permission, _, short_report = evaluate(open_in_group, intent(side="SHORT"))
    long_permission, _, long_report = evaluate(open_in_group, intent(side="LONG", stop=98.0))
    assert short_permission == TradingPermission.DENY
    assert "PORTFOLIO_GROUP_RISK" in short_report.violated_invariants, "3,5 % в одну сторону > 3 %"
    assert "PORTFOLIO_GROUP_RISK" not in long_report.violated_invariants, "LONG против SHORT группы — не ставка в ту же сторону"


def test_a_position_without_a_stop_counts_its_whole_size_as_risk():
    permission, _, report = evaluate(exposure(position("DOGEUSDT", size=10.0, stop=0.0)))
    assert permission == TradingPermission.DENY
    assert "PORTFOLIO_OPEN_RISK" in report.violated_invariants, "10 $ без стопа + 1 $ новой = 11 %"


def test_the_process_risk_core_takes_the_limits_from_settings(monkeypatch):
    monkeypatch.setattr(config, "RISK_MAX_OPEN_POSITIONS", 4)
    monkeypatch.setattr(rc, "_risk_core", None)
    limits = rc.get_risk_core().config
    assert limits.max_open_positions == 4
    assert limits.max_single_position_pct == config.RISK_MAX_SINGLE_POSITION_PCT
    assert limits.max_open_risk_pct == config.RISK_MAX_OPEN_RISK_PCT


def test_the_production_limits_by_default():
    limits = rc.config_from_settings()
    assert (limits.max_single_position_pct, limits.max_aggregate_exposure_pct, limits.max_correlated_group_pct) \
        == (100.0, 300.0, 150.0)
    assert (limits.max_open_positions, limits.max_open_risk_pct, limits.max_group_risk_pct) == (6, 6.0, 3.0)


def test_the_adr_defaults_stay_for_the_invariant_tests():
    limits = RiskCoreConfig()
    assert (limits.max_single_position_pct, limits.max_aggregate_exposure_pct, limits.max_correlated_group_pct) \
        == (10.0, 50.0, 30.0)


def test_the_signal_generator_does_not_send_a_zero_size():
    source = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    sized = source.index("pos_size = position_size(entry, stop, side)")
    skip = source.index("if not pos_size:", sized)
    assert skip < source.index("continue", skip) < source.index("lev = calculate_leverage(", sized)


@pytest.mark.parametrize("profile, risk", [("PAPER_TRADING=false", "RISK_PERCENT=1"),
                                           ("PAPER_TRADING=true", "RISK_PERCENT=2")])
def test_the_mode_profiles_set_the_risk_per_trade(profile, risk):
    from tests.test_deploy_artifacts import DEPLOY, step_body
    step = step_body((DEPLOY / "deploy.sh").read_text(encoding="utf-8"), "step_mode")
    line = next(line for line in step.splitlines() if profile in line and "pairs=" in line)
    assert risk in line
