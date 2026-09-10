"""
Второй рубеж предохранителя — в исполнителе ордеров (аудит 10.09.2026, B-3).

Раньше OrderExecutor.execute() проверял только размер и признак «сухого»
режима: вызов в обход гейткипера отправлял ордер при любой паузе. Теперь
открытие блокируется ДО первого обращения к бирже, а закрытие — нет: остановка
торговли означает «не наращивать риск», а закрытие риск снижает.

Биржа подменена клиентом, который записывает обращения; предохранитель
подменяется, чтобы исход зависел только от него.
"""
import pytest

from execution import order_executor as oe


class ReachedExchange(Exception):
    """Клиент биржи дошёл до отправки ордера — дальше в тесте идти не нужно."""


class FakeClient:
    _testnet = True

    def __init__(self):
        self.calls = []

    def get_instrument_filters(self, symbol):
        from decimal import Decimal
        from exchange.bybit_client import InstrumentFilters
        self.calls.append(("instruments", symbol))
        return InstrumentFilters(
            symbol=symbol, status="Trading", tick_size=Decimal("0.1"), qty_step=Decimal("0.001"),
            min_qty=Decimal("0.001"), max_market_qty=Decimal("100"), min_notional=Decimal("5"),
            max_leverage=Decimal("100"),
        )

    def get_mark_price(self, symbol):
        self.calls.append(("mark", symbol))
        return 65000.0

    def set_leverage(self, symbol, leverage):
        self.calls.append(("set_leverage", symbol))

    def place_order(self, *args, **kwargs):
        self.calls.append(("place_order", kwargs.get("symbol") or (args[0] if args else None)))
        raise ReachedExchange()


@pytest.fixture
def testnet_env(monkeypatch):
    """Режим, в котором ордера действительно уходят на биржу."""
    for name in ("LIVE_TRADING", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("DRY_RUN", "false")


@pytest.fixture
def halted(monkeypatch):
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: "торговля приостановлена вручную (/pause)")


@pytest.fixture
def clear(monkeypatch):
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: None)


def _request():
    return oe.TradeRequest(symbol="BTCUSDT", side="LONG", qty=0.01, entry_price=None, stop_loss=60000.0,
                           leverage=2.0)


def test_open_is_blocked_before_touching_exchange(testnet_env, halted):
    client = FakeClient()
    executor = oe.OrderExecutor(client=client)
    assert executor._dry_run is False, "тест имеет смысл только для реальных ордеров"

    result = executor.execute(_request())

    assert result.success is False
    assert result.error.startswith("Trading halted")
    assert client.calls == [], f"при паузе исполнитель не должен обращаться к бирже, а обратился: {client.calls}"


def test_open_proceeds_when_not_halted(testnet_env, clear):
    """
    Обратная сторона: без остановки исполнитель доходит до биржи. Без этой
    проверки тест выше проходил бы и на исполнителе, который не шлёт ничего никогда.
    """
    client = FakeClient()
    executor = oe.OrderExecutor(client=client)
    try:
        executor.execute(_request())
    except ReachedExchange:
        pass
    assert ("place_order", "BTCUSDT") in client.calls or any(c[0] == "place_order" for c in client.calls)


def test_close_is_not_blocked_by_halt(testnet_env, halted):
    """Закрытие обязано проходить и при остановке: оно снижает риск."""
    client = FakeClient()
    executor = oe.OrderExecutor(client=client)
    with pytest.raises(ReachedExchange):
        executor.close_position("BTCUSDT", "LONG", 0.01)
    assert any(c[0] == "place_order" for c in client.calls)


def test_dry_run_does_not_consult_kill_switch(monkeypatch):
    """В «сухом» режиме побочных эффектов нет, предохранитель не нужен и не зовётся."""
    for name in ("LIVE_TRADING", "PAPER_TRADING", "BYBIT_TESTNET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DRY_RUN", "true")

    import execution.kill_switch as ks
    consulted = []
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: consulted.append(1) or "halt")

    executor = oe.OrderExecutor(client=FakeClient())
    assert executor._dry_run is True
    executor.execute(_request())
    assert consulted == []
