"""
Тесты для RiskCore.
"""
import pytest
from datetime import datetime, UTC, timedelta
from core.risk_core import (
    RiskCore,
    RiskCoreConfig,
    RiskState,
    TradingPermission,
    TradingIntent,
    CapitalSnapshot,
    ExposureSnapshot,
    BehavioralCounters,
    SystemHealthFlags,
)


# ========== Fixtures ==========

@pytest.fixture
def risk_core():
    return RiskCore()


@pytest.fixture
def healthy_intent():
    return TradingIntent(
        symbol="BTCUSDT",
        side="LONG",
        position_size_usd=100.0,
        entry_price=50000.0,
        stop_price=49000.0,
    )


@pytest.fixture
def healthy_capital():
    return CapitalSnapshot(
        current_balance_usd=10000.0,
        initial_balance_usd=10000.0,
        total_loss_usd=0.0,
        loss_24h_usd=0.0,
        loss_7d_usd=0.0,
    )


@pytest.fixture
def healthy_exposure():
    return ExposureSnapshot(
        open_positions=[],
        total_exposure_usd=0.0,
        max_single_position_usd=0.0,
        correlation_groups={},
    )


@pytest.fixture
def healthy_behavioral():
    return BehavioralCounters(
        actions_last_hour=0,
        actions_last_24h=0,
        consecutive_losses=0,
    )


@pytest.fixture
def healthy_system():
    return SystemHealthFlags(
        is_safe_mode=False,
        consecutive_errors=0,
        runtime_healthy=True,
        critical_modules_available=True,
    )


# ========== Tests ==========

class TestRiskCoreHappyPath:
    def test_healthy_inputs_returns_allow(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_behavioral, healthy_system
    ):
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure,
            healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.ALLOW

    def test_initial_state_is_safe(self, risk_core):
        assert risk_core.risk_state == RiskState.SAFE


class TestRiskCoreCapitalInvariants:
    def test_zero_balance_returns_deny(
        self, risk_core, healthy_intent, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        capital = CapitalSnapshot(
            current_balance_usd=0.0,
            initial_balance_usd=10000.0,
            total_loss_usd=10000.0,
            loss_24h_usd=0.0,
            loss_7d_usd=0.0,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, capital, healthy_exposure,
            healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY

    def test_drawdown_exceeds_limit_returns_deny(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_behavioral, healthy_system
    ):
        # 20% drawdown (equals max_absolute_loss_pct default)
        capital = CapitalSnapshot(
            current_balance_usd=8000.0,
            initial_balance_usd=10000.0,
            total_loss_usd=2000.0,  # 20% of initial
            loss_24h_usd=0.0,
            loss_7d_usd=0.0,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, capital, healthy_exposure,
            healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY


class TestRiskCoreExposureInvariants:
    def test_position_too_large_blocks(
        self, risk_core, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        # 11% of balance — exceeds max_single_position_pct=10%
        intent = TradingIntent(
            symbol="BTCUSDT",
            side="LONG",
            position_size_usd=1100.0,
            entry_price=50000.0,
            stop_price=49000.0,
        )
        permission, state, report = risk_core.evaluate(
            intent, healthy_capital, healthy_exposure,
            healthy_behavioral, healthy_system
        )
        # Single position cap violation → LIMITED state → ALLOW_LIMITED
        assert permission == TradingPermission.ALLOW_LIMITED
        assert len(report.violations) > 0


class TestRiskCoreBehavioralInvariants:
    def test_too_many_actions_last_hour_blocks(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_system
    ):
        behavioral = BehavioralCounters(
            actions_last_hour=10,  # equals max_actions_per_hour=10
            actions_last_24h=0,
            consecutive_losses=0,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure,
            behavioral, healthy_system
        )
        assert permission != TradingPermission.ALLOW

    def test_single_recent_loss_does_not_block(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_system
    ):
        """
        Один убыток паузу не включает.

        Тест раньше требовал DENY при одном убытке и падал: порог подняли
        намеренно коммитом 4403b2b — с `consecutive_losses > 0` до `>= 3`
        с пропорциональной паузой 30/45/60 минут. Прежнее правило
        останавливало торговлю после любого убытка, то есть примерно всегда.
        """
        recent = datetime.now(UTC) - timedelta(minutes=5)
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=1,
            last_loss_timestamp=recent,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure,
            behavioral, healthy_system
        )
        assert permission != TradingPermission.DENY

    def test_three_consecutive_losses_within_cooldown_blocks(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_system
    ):
        """Три убытка подряд и 5 минут с последнего — пауза 30 минут ещё идёт."""
        recent = datetime.now(UTC) - timedelta(minutes=5)
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=3,
            last_loss_timestamp=recent,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure,
            behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY

    def test_three_consecutive_losses_after_cooldown_allows(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_system
    ):
        """
        Та же серия, но пауза истекла — запрет обязан сняться сам.
        Без этой проверки предыдущий тест проходил бы и на коде, который
        блокирует по счётчику убытков навсегда, игнорируя время.
        """
        long_ago = datetime.now(UTC) - timedelta(minutes=45)
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=3,
            last_loss_timestamp=long_ago,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure,
            behavioral, healthy_system
        )
        assert permission != TradingPermission.DENY


class TestRiskCoreSystemHealth:
    def test_system_not_ready_blocks(
        self, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_behavioral
    ):
        system = SystemHealthFlags(
            is_safe_mode=False,
            consecutive_errors=0,
            runtime_healthy=False,
            critical_modules_available=True,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure,
            healthy_behavioral, system
        )
        assert permission == TradingPermission.DENY


class TestRiskCoreCapitalTimebound:
    def test_24h_loss_blocks(
        self, risk_core, healthy_intent, healthy_exposure, healthy_behavioral, healthy_system
    ):
        capital = CapitalSnapshot(
            current_balance_usd=10000.0,
            initial_balance_usd=10000.0,
            total_loss_usd=0.0,
            loss_24h_usd=500.0,  # 5% of 10000 — equals max_loss_24h_pct
            loss_7d_usd=0.0,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY

    def test_7d_loss_blocks(
        self, risk_core, healthy_intent, healthy_exposure, healthy_behavioral, healthy_system
    ):
        capital = CapitalSnapshot(
            current_balance_usd=10000.0,
            initial_balance_usd=10000.0,
            total_loss_usd=0.0,
            loss_24h_usd=0.0,
            loss_7d_usd=1000.0,  # 10% of 10000 — equals max_loss_7d_pct
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY


class TestRiskCoreExposureAggregate:
    def test_aggregate_exposure_blocks(
        self, risk_core, healthy_capital, healthy_behavioral, healthy_system
    ):
        # 4900 + 200 = 5100 = 51% > 50% max_aggregate_exposure_pct
        exposure = ExposureSnapshot(
            open_positions=[],
            total_exposure_usd=4900.0,
            max_single_position_usd=4900.0,
            correlation_groups={},
        )
        intent = TradingIntent(
            symbol="BTCUSDT",
            side="LONG",
            position_size_usd=200.0,
            entry_price=50000.0,
            stop_price=49000.0,
        )
        permission, state, report = risk_core.evaluate(
            intent, healthy_capital, exposure, healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY

    def test_correlated_group_blocks(
        self, risk_core, healthy_capital, healthy_behavioral, healthy_system
    ):
        # 3100 / 10000 = 31% > 30% max_correlated_group_pct
        exposure = ExposureSnapshot(
            open_positions=[],
            total_exposure_usd=0.0,
            max_single_position_usd=0.0,
            correlation_groups={"layer1": ["BTCUSDT"]},
        )
        intent = TradingIntent(
            symbol="BTCUSDT",
            side="LONG",
            position_size_usd=3100.0,
            entry_price=50000.0,
            stop_price=49000.0,
        )
        permission, state, report = risk_core.evaluate(
            intent, healthy_capital, exposure, healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY


class TestRiskCoreBehavioralActions24h:
    def test_too_many_actions_24h_blocks(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure, healthy_system
    ):
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=50,  # >= max_actions_per_24h=50 → LOCKED
            consecutive_losses=0,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY

    def test_action_cooldown_blocks(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure, healthy_system
    ):
        recent = datetime.now(UTC) - timedelta(seconds=30)  # 30s < 60s cooldown
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=0,
            last_action_timestamp=recent,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, behavioral, healthy_system
        )
        assert permission != TradingPermission.ALLOW


class TestRiskCoreSystemicFlags:
    def test_critical_modules_unavailable_blocks(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral
    ):
        system = SystemHealthFlags(
            is_safe_mode=False,
            consecutive_errors=0,
            runtime_healthy=True,
            critical_modules_available=False,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, system
        )
        assert permission == TradingPermission.DENY

    def test_consecutive_errors_blocks(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral
    ):
        system = SystemHealthFlags(
            is_safe_mode=False,
            consecutive_errors=5,  # >= max_consecutive_errors=5
            runtime_healthy=True,
            critical_modules_available=True,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, system
        )
        assert permission == TradingPermission.DENY

    def test_safe_mode_flag_blocks(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral
    ):
        system = SystemHealthFlags(
            is_safe_mode=True,
            consecutive_errors=0,
            runtime_healthy=True,
            critical_modules_available=True,
        )
        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, system
        )
        assert permission == TradingPermission.DENY


class TestRiskCoreReset:
    def test_reset_from_limited_clears_state(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure, healthy_system
    ):
        # Drive state to LIMITED via action cooldown violation
        recent = datetime.now(UTC) - timedelta(seconds=30)
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=0,
            last_action_timestamp=recent,
        )
        risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, behavioral, healthy_system
        )
        assert risk_core.risk_state == RiskState.LIMITED
        risk_core.reset()
        assert risk_core.risk_state == RiskState.SAFE

    def test_reset_from_halted_does_nothing(self, risk_core):
        invalid_intent = TradingIntent(
            symbol="BTCUSDT",
            side="INVALID",  # invalid → HALTED
            position_size_usd=100.0,
            entry_price=50000.0,
            stop_price=49000.0,
        )
        capital = CapitalSnapshot(
            current_balance_usd=10000.0,
            initial_balance_usd=10000.0,
            total_loss_usd=0.0,
            loss_24h_usd=0.0,
            loss_7d_usd=0.0,
        )
        exposure = ExposureSnapshot(
            open_positions=[],
            total_exposure_usd=0.0,
            max_single_position_usd=0.0,
            correlation_groups={},
        )
        behavioral = BehavioralCounters(
            actions_last_hour=0,
            actions_last_24h=0,
            consecutive_losses=0,
        )
        system = SystemHealthFlags(
            is_safe_mode=False,
            consecutive_errors=0,
            runtime_healthy=True,
            critical_modules_available=True,
        )
        risk_core.evaluate(invalid_intent, capital, exposure, behavioral, system)
        assert risk_core.risk_state == RiskState.HALTED
        risk_core.reset()
        assert risk_core.risk_state == RiskState.HALTED


class TestRiskCoreFailClosed:
    def test_negative_position_size_returns_deny(
        self, risk_core, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        intent = TradingIntent(
            symbol="BTCUSDT",
            side="LONG",
            position_size_usd=-100.0,  # invalid
            entry_price=50000.0,
            stop_price=49000.0,
        )
        permission, state, report = risk_core.evaluate(
            intent, healthy_capital, healthy_exposure,
            healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY

    def test_invalid_side_returns_deny(
        self, risk_core, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        intent = TradingIntent(
            symbol="BTCUSDT",
            side="BUY",  # invalid — must be LONG or SHORT
            position_size_usd=100.0,
            entry_price=50000.0,
            stop_price=49000.0,
        )
        permission, state, report = risk_core.evaluate(
            intent, healthy_capital, healthy_exposure,
            healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY


from core.risk_core import RiskState as _RiskState  # noqa: E402


class TestRiskCoreHaltLatch:
    """
    HALTED обязан держаться до ручного сброса (аудит 10.09.2026, C5).

    Раньше evaluate() перезаписывал состояние на каждом вызове, и HALTED
    снимался следующим же нормальным сигналом — при том что ADR называет его
    терминальным, а reset() отказывался его сбрасывать.
    """

    @staticmethod
    def _invalid_intent():
        return TradingIntent(
            symbol="BTCUSDT", side="BUY", position_size_usd=100.0,
            entry_price=50000.0, stop_price=49000.0,
        )

    def test_healthy_inputs_are_not_denied_without_prior_halt(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        """Точка отсчёта: без предшествующего HALTED здоровый сигнал проходит."""
        permission, state, _ = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert permission != TradingPermission.DENY
        assert state != _RiskState.HALTED

    def test_halt_survives_next_valid_signal(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        _, first_state, _ = risk_core.evaluate(
            self._invalid_intent(), healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert first_state == _RiskState.HALTED

        permission, state, report = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY
        assert state == _RiskState.HALTED
        assert "HALT_LATCHED" in report.violated_invariants
        assert risk_core.risk_state == _RiskState.HALTED

    def test_plain_reset_does_not_release_halt(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        """reset() — для счётчиков и тестов; HALTED он снимать не должен."""
        risk_core.evaluate(
            self._invalid_intent(), healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        risk_core.reset()
        _, state, _ = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert state == _RiskState.HALTED

    def test_manual_release_restores_trading(
        self, risk_core, healthy_intent, healthy_capital, healthy_exposure,
        healthy_behavioral, healthy_system
    ):
        risk_core.evaluate(
            self._invalid_intent(), healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert risk_core.halt_latched is True
        assert risk_core.reset_halt(by="test") is True
        assert risk_core.halt_latched is False

        permission, state, _ = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert permission != TradingPermission.DENY
        assert state != _RiskState.HALTED

    def test_release_without_halt_is_noop(self, risk_core):
        assert risk_core.reset_halt(by="test") is False

    def test_exception_inside_evaluation_latches(
        self, monkeypatch, risk_core, healthy_intent, healthy_capital,
        healthy_exposure, healthy_behavioral, healthy_system
    ):
        """Исключение внутри Risk Core — это ошибка кода, и она тоже защёлкивает."""
        def boom(*args, **kwargs):
            raise RuntimeError("bug inside an invariant check")

        monkeypatch.setattr(risk_core, "_check_systemic_invariants", boom)
        _, state, _ = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert state == _RiskState.HALTED

        monkeypatch.undo()  # проверка больше не падает — но защёлка должна держать
        permission, state, _ = risk_core.evaluate(
            healthy_intent, healthy_capital, healthy_exposure, healthy_behavioral, healthy_system
        )
        assert permission == TradingPermission.DENY
        assert state == _RiskState.HALTED
