"""
Журнал действий Risk Core (10.09.2026, задача 3.7).

До исправления add_signal не вызывался нигде: лимиты действий в час и сутки и
пауза между действиями видели пустой журнал и не срабатывали никогда.
"""
import ast
import pathlib
from datetime import datetime, timedelta, UTC

from execution.gatekeeper import _signal_times

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_signal_times_accept_datetimes_and_iso_strings():
    now = datetime.now(UTC)
    naive = datetime(2026, 9, 10, 12, 0, 0)
    times = _signal_times([
        {"timestamp": now},
        {"timestamp": "2026-09-10T12:00:00Z"},
        {"timestamp": naive},
        {"timestamp": "не дата"},
        {"symbol": "без времени"},
        "не словарь",
    ])
    assert times[0] == now
    assert times[1] == datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC), "так вернутся сигналы из снимка состояния"
    assert times[2].tzinfo is not None
    assert len(times) == 3, "мусор пропускается, а не роняет проверку в отказ"


def test_signal_times_feed_hourly_counts():
    now = datetime.now(UTC)
    times = _signal_times([{"timestamp": now - timedelta(minutes=m)} for m in (1, 30, 59, 61, 600)])
    assert sum(1 for t in times if (now - t).total_seconds() < 3600) == 3


def test_approval_is_recorded_in_the_action_journal():
    text = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(text))
                if isinstance(n, ast.FunctionDef) and n.name == "send_signal")
    src = ast.get_source_segment(text, node)
    record = "system_state.add_signal({"
    assert record in src
    assert src.index("finalize_position_size(") < src.index(record) < src.rindex("return True"), \
        "в журнал попадает только одобренное действие"
