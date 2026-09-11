"""
Правило перегрузки MetaDecisionBrain (SOFT BLOCK при > 10 сигналов «за период»).

Найдено 11.09.2026 на проде: сделки встали на сутки. Gatekeeper передавал в
правило длину system_state.recent_signals целиком, а список по времени не
чистится и возвращается из снимка состояния при старте. После 11 отправленных
10.09 сигналов каждый следующий получал «Too many signals in recent period
(11)», перезапуски не помогали. Теперь считаются сигналы за последний час.
"""
import inspect
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from execution import gatekeeper

NOW = datetime(2026, 9, 11, 13, 0, tzinfo=UTC)


def state(*ages_minutes, as_strings=False):
    signals = []
    for age in ages_minutes:
        ts = NOW - timedelta(minutes=age)
        signals.append({"symbol": "SOLUSDT", "timestamp": ts.isoformat() if as_strings else ts})
    return SimpleNamespace(recent_signals=signals)


def test_yesterdays_signals_no_longer_count():
    """Ровно прод 11.09: 11 сигналов вчера около 13:00 UTC — сегодня это не перегрузка."""
    yesterday = state(*[24 * 60 + 30 + i * 4 for i in range(11)])
    assert gatekeeper._recent_signal_count(yesterday, now=NOW) == 0


def test_a_burst_within_the_hour_still_counts():
    assert gatekeeper._recent_signal_count(state(*range(0, 55, 5)), now=NOW) == 11


def test_only_the_last_hour_is_counted():
    assert gatekeeper._recent_signal_count(state(5, 30, 59, 61, 120, 1500), now=NOW) == 3


def test_signals_restored_from_a_snapshot_are_parsed():
    """Из снимка состояния метки времени возвращаются ISO-строками."""
    assert gatekeeper._recent_signal_count(state(10, 20, 90, as_strings=True), now=NOW) == 2


def test_no_state_means_no_signals():
    assert gatekeeper._recent_signal_count(None, now=NOW) == 0
    assert gatekeeper._recent_signal_count(SimpleNamespace(), now=NOW) == 0


def test_the_meta_brain_gets_the_windowed_count():
    source = inspect.getsource(gatekeeper)
    assert "signals_count_recent = _recent_signal_count(system_state)" in source
    assert "len(system_state.recent_signals)" not in source
    assert gatekeeper.META_OVERTRADING_WINDOW_SECONDS == 3600
