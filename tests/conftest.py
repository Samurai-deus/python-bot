"""
Общие fixtures для тест-сьюта market_bot.
"""
import pytest
from dataclasses import dataclass
from core.decision_core import MarketRegime, RiskExposure, CognitiveState


# ========== MockSystemState для DecisionCore ==========

@dataclass
class _SystemHealth:
    safe_mode: bool = False
    consecutive_errors: int = 0


class MockSystemState:
    def __init__(self):
        self.system_health = _SystemHealth()
        self.cognitive_state: CognitiveState | None = None
        self.market_regime: MarketRegime | None = None
        self.risk_state: RiskExposure | None = None
        self.opportunities: dict = {}
        self._can_trade_val = True

    def update_trading_decision(self, val: bool):
        self._can_trade_val = val


@pytest.fixture
def system_state():
    return MockSystemState()


# ========== MockPortfolioState для PositionSizer ==========

class MockPortfolioState:
    def __init__(self, exposure=0.0, available_ratio=1.0):
        self._exposure = exposure
        self._ratio = available_ratio

    def total_exposure(self) -> float:
        return self._exposure

    def available_risk_ratio(self) -> float:
        return self._ratio


@pytest.fixture
def empty_portfolio():
    return MockPortfolioState(exposure=0.0, available_ratio=1.0)


@pytest.fixture
def full_portfolio():
    return MockPortfolioState(exposure=1.0, available_ratio=0.0)


# ========== Тесты не ходят во внешнюю сеть ==========
# 15.09.2026: тест данных клиента И14 стал звать Bybit (instruments-info) — локально прошёл, в CI упал 403
# (CloudFront режет страну). Любой внешний HTTP из теста — ошибка сразу и везде, а не сюрприз в CI.
# Локальные адреса (TestClient, тестовые сокеты) разрешены; httpx.MockTransport в тестах реальный транспорт не трогает.

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}


def _host_of(url) -> str:
    from urllib.parse import urlparse
    return (urlparse(str(url)).hostname or "").lower()


@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    import requests

    def blocked_request(self, method, url, *args, **kwargs):
        if _host_of(url) in _LOCAL_HOSTS:
            return _orig_request(self, method, url, *args, **kwargs)
        raise RuntimeError(f"тест ходит во внешнюю сеть: {method} {url} — подмените транспорт или клиент")

    _orig_request = requests.Session.request
    monkeypatch.setattr(requests.Session, "request", blocked_request)
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return
    _orig_handle = httpx.HTTPTransport.handle_request

    def blocked_handle(self, request):
        if _host_of(request.url) in _LOCAL_HOSTS:
            return _orig_handle(self, request)
        raise RuntimeError(f"тест ходит во внешнюю сеть: {request.method} {request.url} — используйте httpx.MockTransport")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked_handle)


@pytest.fixture(autouse=True)
def signal_trading_machinery_on(monkeypatch, tmp_path):
    """
    С 29.09.2026 сигнальная торговля по умолчанию ВЫКЛЮЧЕНА (utils.env.signal_trading_enabled), как и её
    предохранители: свой субсчёт (SIGNAL_ACCOUNT_DEDICATED) и стратегии (SIGNAL_STRATEGIES). Тесты
    проверяют механику (выключатель, Risk Core, капитал, журнал, стратегии) — включаем явно; значения по
    умолчанию проверяют test_env_defaults и test_signal_safety, сценарии «счёт отдан И14» ставят false сами.
    Ручная пауза и защёлка Risk Core пишутся в файлы — в тестах во временный каталог, не в /data/db.
    """
    monkeypatch.setenv("SIGNAL_TRADING_ENABLED", "true")
    monkeypatch.setenv("SIGNAL_ACCOUNT_DEDICATED", "true")
    monkeypatch.setenv("SIGNAL_STRATEGIES", "trend_following,mean_reversion,momentum_breakout,legacy")
    monkeypatch.setenv("MANUAL_PAUSE_FILE", str(tmp_path / "manual_pause"))
    monkeypatch.setenv("RISK_HALT_FILE", str(tmp_path / "risk_halt"))


@pytest.fixture(autouse=True)
def no_real_owner_messages(request, monkeypatch):
    """
    Сообщение владельцу из теста — ошибка сразу. 29.09.2026: тесты И13 (время — понедельник днём) слали
    еженедельную сводку; сеть блокировалась выше, но send_owner_blocking глотал ошибку и повторял с паузами
    2 и 4 с — файл шёл 75 с вместо 4, а тест оставался зелёным. Тест самой отправки — test_telegram_owner.
    """
    if request.module.__name__.endswith("test_telegram_owner"):
        return
    import telegram_bot

    def refuse(text, attempts=3):
        raise AssertionError(f"тест отправляет сообщение владельцу: {text[:80]!r} — подмените notify")
    monkeypatch.setattr(telegram_bot, "send_owner_blocking", refuse)
