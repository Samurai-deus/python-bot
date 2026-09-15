"""
Гигиена программы (после 14–15.09.2026):
  • календарь уведомлений — зеркало плана: каждая контрольная дата упомянута в docs/TRADER_PLAN.md;
  • журнал ребалансировок не теряет повторов (14.09 второй прогон перезаписал запись понедельника);
  • тесты не ходят во внешнюю сеть (CI без Bybit).
"""
import pathlib

import pytest
import requests

from portfolio import calendar
from portfolio.store import Store

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_calendar_checkpoint_is_in_the_plan():
    plan = (ROOT / "docs" / "TRADER_PLAN.md").read_text(encoding="utf-8")
    missing = []
    for day, text in calendar.CHECKPOINTS:
        y, m, d = day.split("-")
        if f"{d}.{m}" not in plan:
            missing.append(day)
    assert missing == [], f"даты календаря нет в плане (календарь правится тем же PR, что план): {missing}"
    assert len(calendar.CHECKPOINTS) == len({d for d, _ in calendar.CHECKPOINTS}), "даты не повторяются"


def test_rebalance_reruns_are_kept_in_the_run_log(tmp_path):
    s = Store(str(tmp_path / "p.db"))
    s.rebalance(100, 1000, {"AUSDT": 0.1}, [["AUSDT", "Buy", "1"]], [])
    s.rebalance(100, 2000, {"AUSDT": 0.2, "BUSDT": -0.1}, [["BUSDT", "Sell", "2"]], [["CUSDT", "Buy", "1", "err"]])
    latest = s.conn.execute("SELECT done_ms, weights FROM rebalances WHERE t = 100").fetchall()
    assert len(latest) == 1 and latest[0][0] == 2000, "в rebalances — последний прогон понедельника"
    runs = s.conn.execute("SELECT done_ms FROM rebalance_runs WHERE t = 100 ORDER BY done_ms").fetchall()
    assert [r[0] for r in runs] == [1000, 2000], "оба прогона сохранены в журнале"
    assert s.has_rebalance(100) and not s.has_rebalance(200)


def test_tests_cannot_reach_external_hosts():
    with pytest.raises(RuntimeError, match="внешнюю сеть"):
        requests.Session().get("https://api.bybit.com/v5/market/time", timeout=1)
    import httpx
    with pytest.raises(RuntimeError, match="внешнюю сеть"):
        httpx.get("https://api.bybit.com/v5/market/time", timeout=1)


def test_program_dates_are_in_the_plan():
    """PROGRAM в api/research_data.py повторяет сроки плана вручную — каждая дата обязана быть в плане."""
    import re
    from api.research_data import PROGRAM
    plan = (ROOT / "docs" / "TRADER_PLAN.md").read_text(encoding="utf-8")
    dates = set()
    for meta in PROGRAM.values():
        for k, v in meta.items():
            if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
                dates.add(v)
    missing = sorted(d for d in dates if f"{d[8:10]}.{d[5:7]}" not in plan)
    assert missing == [], f"дат PROGRAM нет в плане: {missing}"


def test_notification_section_dates_are_in_the_calendar():
    """Обратная проверка: даты из раздела «Уведомления владельцу» плана есть в календаре."""
    import re
    plan = (ROOT / "docs" / "TRADER_PLAN.md").read_text(encoding="utf-8")
    section = plan.split("## Уведомления владельцу", 1)[1].split("\n## ", 1)[0]
    listed = set(re.findall(r"\b(\d{2})\.(\d{2})(?:\.(\d{4}))?\b", section.split("**Контрольные даты**", 1)[1].split("\n", 1)[0]))
    cal = {(d[8:10], d[5:7]) for d, _ in calendar.CHECKPOINTS}
    missing = sorted(f"{dd}.{mm}" for dd, mm, _ in listed if (dd, mm) not in cal)
    assert missing == [], f"в разделе «Уведомления» есть даты вне календаря: {missing}"
