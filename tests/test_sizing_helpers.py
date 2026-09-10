"""
Разбор цены входа и стопа из сигнала. signal_generator кладёт их и на верхний
уровень signal_data, и в signal_data["zone"]; Risk Core читал из zone,
исполнитель — сверху. Функции берут верхний уровень, иначе zone.
"""
import pytest

from execution.sizing_guard import entry_price_from_signal, stop_distance_from_signal


def test_top_level_prices_are_used():
    assert entry_price_from_signal({"entry": 100.0, "stop": 98.0}) == 100.0
    assert stop_distance_from_signal({"entry": 100.0, "stop": 98.0}) == pytest.approx(0.02)


def test_zone_is_used_when_top_level_is_missing():
    signal = {"zone": {"entry": 200.0, "stop": 190.0}}
    assert entry_price_from_signal(signal) == 200.0
    assert stop_distance_from_signal(signal) == pytest.approx(0.05)


def test_short_stop_above_entry_gives_positive_distance():
    assert stop_distance_from_signal({"entry": 100.0, "stop": 103.0}) == pytest.approx(0.03)


@pytest.mark.parametrize("signal", [
    None, {}, {"entry": 100.0}, {"stop": 98.0},
    {"entry": 100.0, "stop": 100.0},
    {"entry": 0, "stop": 98.0}, {"entry": "abc", "stop": 98.0},
])
def test_unusable_signals_give_none(signal):
    assert stop_distance_from_signal(signal) is None
