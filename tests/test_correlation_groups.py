"""
Группы коррелирующих символов для Risk Core (решение владельца 11.09.2026).

До 11.09 гейткипер передавал Risk Core пустой словарь групп — лимит на группу
не работал. Здесь — синтетический рынок: три символа следуют одному фактору,
четвёртый независим; база — временная SQLite.
"""
import math
import pathlib
import random
from datetime import datetime, timedelta, UTC

import pytest

import database
from market_data import correlation_groups as cg

ROOT = pathlib.Path(__file__).resolve().parent.parent
GROUP = ["AAAUSDT", "BBBUSDT", "CCCUSDT"]


def candles(returns, start=1_700_000_000_000, step=14_400_000):
    price, rows = 100.0, [[str(start), "0", "0", "0", "100.0", "0"]]
    for k, r in enumerate(returns, start=1):
        price *= math.exp(r)
        rows.append([str(start + k * step), "0", "0", "0", repr(price), "0"])
    return rows


def factor_market(n=179, seed=7):
    rnd = random.Random(seed)
    market = [rnd.gauss(0, 0.02) for _ in range(n)]

    def follower():
        return candles([m + rnd.gauss(0, 0.004) for m in market])

    data = {s: follower() for s in GROUP}
    data["LONEUSDT"] = candles([rnd.gauss(0, 0.02) for _ in range(n)])
    return data


def fetch_from(data):
    return lambda symbol, interval, limit: data[symbol]


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "groups.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    cg.clear_cache()
    yield database
    cg.clear_cache()
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


# ---------------------------------------------------------------------------
# Расчёт
# ---------------------------------------------------------------------------

def test_followers_of_one_factor_form_a_group_and_the_loner_does_not():
    data = factor_market()
    assert cg.cluster(list(data), cg.correlation_matrix(data)) == {"corr-1": GROUP}


def test_average_linkage_does_not_chain():
    """A похож на B, B на C, но A не похож на C — A и C в одну группу не попадают."""
    matrix = {}
    for a, b, r in (("A", "B", 0.9), ("B", "C", 0.9), ("A", "C", 0.1)):
        matrix[(a, b)] = matrix[(b, a)] = r
    groups = cg.cluster(["A", "B", "C"], matrix)
    assert len(groups) == 1 and len(groups["corr-1"]) == 2
    assert not any({"A", "C"} <= set(g) for g in groups.values())


def test_short_overlap_is_not_a_correlation():
    """Новый листинг с парой общих свечей не сливается ни с кем."""
    data = factor_market()
    data["NEWUSDT"] = data["AAAUSDT"][-10:]
    matrix = cg.correlation_matrix(data)
    assert ("NEWUSDT", "AAAUSDT") not in matrix


# ---------------------------------------------------------------------------
# Хранение и пересчёт
# ---------------------------------------------------------------------------

def test_no_groups_yet_means_empty_and_refresh_is_due(db):
    assert cg.get_groups() == {}
    assert cg.needs_refresh()


def test_refresh_is_stored_and_read_back(db):
    data = factor_market()
    assert cg.refresh(symbols=list(data), fetch=fetch_from(data)) == {"corr-1": GROUP}
    cg.clear_cache()
    assert cg.get_groups() == {"corr-1": GROUP}
    assert not cg.needs_refresh()
    assert cg.needs_refresh(now=datetime.now(UTC) + timedelta(hours=25))


def test_failed_refresh_keeps_previous_groups(db):
    data = factor_market()
    cg.refresh(symbols=list(data), fetch=fetch_from(data))
    assert cg.refresh(symbols=list(data), fetch=lambda symbol, interval, limit: []) is None
    cg.clear_cache()
    assert cg.get_groups() == {"corr-1": GROUP}


# ---------------------------------------------------------------------------
# Подключение
# ---------------------------------------------------------------------------

def test_gatekeeper_passes_real_groups_to_risk_core():
    text = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert "correlation_groups = get_groups()" in text
    assert "correlation_groups = {}" not in text


def test_runner_refreshes_groups_in_the_background():
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    assert 'name="CorrelationGroups"' in text
