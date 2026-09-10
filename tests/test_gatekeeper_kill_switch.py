"""
Предохранитель в гейткипере (аудит 10.09.2026, B-3).

Это единственный рубеж для бумажной торговли: исполнитель ордеров в режиме
PAPER не вызывается, а бумажную сделку signal_generator открывает, только если
send_signal() вернул True. Значит, при паузе send_signal обязан вернуть False —
и сделать это раньше любой работы, включая чтение базы.

Гейткипер создаётся в обход тяжёлого __init__: проверяемый путь до точки
предохранителя атрибутов экземпляра не использует. Базу подменяет регистратор,
который останавливает выполнение сразу после вызова.
"""
from types import SimpleNamespace

import pytest

import execution.gatekeeper as gk_module
from execution.gatekeeper import Gatekeeper


class _StopAfterDb(Exception):
    """Дальше базы в этих тестах идти не нужно."""


@pytest.fixture(autouse=True)
def quiet_telegram(monkeypatch):
    """Никаких сообщений наружу, даже если путь дойдёт до уведомлений."""
    for name in ("send_message", "send_message_async", "send_chart", "send_chart_async"):
        monkeypatch.setattr(gk_module, name, lambda *a, **k: None, raising=False)


@pytest.fixture
def db_calls(monkeypatch):
    import database
    calls = []

    def recorder():
        calls.append(1)
        raise _StopAfterDb()

    monkeypatch.setattr(database, "get_open_trades", recorder)
    return calls


def _send(gatekeeper):
    return gatekeeper.send_signal(
        symbol="BTCUSDT",
        signal_data={"entry": 100.0, "stop": 99.0, "target": 103.0, "side": "LONG"},
        states={}, directions={}, risk="LOW", score=80, mode="TREND", reasons=[],
        system_state=SimpleNamespace(system_health=SimpleNamespace(trading_paused=False)),
    )


def _bare():
    return Gatekeeper.__new__(Gatekeeper)


def test_paused_signal_is_rejected_before_any_work(monkeypatch, db_calls):
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: "торговля приостановлена вручную (/pause)")

    assert _send(_bare()) is False
    assert db_calls == [], "при паузе гейткипер не должен даже читать базу"


def test_signal_proceeds_past_kill_switch_when_clear(monkeypatch, db_calls):
    """
    Обратная сторона: без остановки обработка идёт дальше. Без этой проверки
    тест выше проходил бы и на гейткипере, который отклоняет всё подряд.
    """
    import execution.kill_switch as ks
    monkeypatch.setattr(ks, "trading_halt_reason", lambda **kw: None)
    try:
        _send(_bare())
    except Exception:
        # Обработчик ошибок send_signal после остановки на базе трогает
        # счётчики экземпляра, которых у «голого» гейткипера нет. Это не важно:
        # проверяемое ниже записано ДО любого исключения.
        pass
    assert db_calls == [1]


def test_pre_check_does_not_consult_risk_core(monkeypatch, db_calls):
    """
    До оценки сигнала состояние Risk Core — от прошлой оценки, возможно по
    другому символу. Отказ по размеру для BTC не должен блокировать ETH.
    """
    import execution.kill_switch as ks
    seen = {}

    def spy(**kwargs):
        seen.update(kwargs)
        return None

    monkeypatch.setattr(ks, "trading_halt_reason", spy)
    try:
        _send(_bare())
    except Exception:
        # Обработчик ошибок send_signal после остановки на базе трогает
        # счётчики экземпляра, которых у «голого» гейткипера нет. Это не важно:
        # проверяемое ниже записано ДО любого исключения.
        pass
    assert seen.get("include_risk_core") is False
