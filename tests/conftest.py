"""
Общие fixtures для тест-сьюта market_bot.
"""
import pytest
from dataclasses import dataclass
from typing import Optional, Dict
from core.decision_core import MarketRegime, RiskExposure, CognitiveState


# ========== MockSystemState для DecisionCore ==========

@dataclass
class _SystemHealth:
    safe_mode: bool = False
    consecutive_errors: int = 0


class MockSystemState:
    def __init__(self):
        self.system_health = _SystemHealth()
        self.cognitive_state: Optional[CognitiveState] = None
        self.market_regime: Optional[MarketRegime] = None
        self.risk_state: Optional[RiskExposure] = None
        self.opportunities: Dict = {}
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
