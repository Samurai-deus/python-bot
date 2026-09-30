"""
Хардкод после аудита 29.09.2026: одно место у каждого числа правила и каждого адреса; булевы флаги
окружения — через env_flag; мини-апп берёт пороги и капитал из данных исполнителя.
"""
import pathlib
import re

import pytest

from api import research_data as rd
from portfolio.store import Store

ROOT = pathlib.Path(__file__).resolve().parent.parent


def code_files():
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if not rel.startswith(("venv/", "archive/", "tests/", "backtest/")):
            yield rel, p.read_text(encoding="utf-8", errors="ignore")


def test_boolean_env_flags_go_through_env_flag():
    """`.lower() == "true"` молча считал «1» и «yes» выключенным — правило utils.env.env_flag."""
    bad = [rel for rel, text in code_files() if re.search(r'environ\.get\([^)]*\)\.lower\(\)\s*(==|in)', text)]
    assert bad == [], bad


def test_bybit_mainnet_url_has_one_source():
    """Адрес основной биржи был в 10 местах; 30.09.2026 — ещё в трёх модулях backtest/ (правило их не видело)."""
    hits = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
            if not p.relative_to(ROOT).as_posix().startswith(("venv/", "archive/", "tests/"))
            and '"https://api.bybit.com' in p.read_text(encoding="utf-8", errors="ignore")]
    assert hits == ["exchange/bybit_client.py"], hits


def test_transient_bybit_codes_have_one_source():
    """Коды временных ошибок Bybit (лимит, внутренняя ошибка) — в exchange.bybit_client, не копиями по клиентам."""
    hits = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
            if not p.relative_to(ROOT).as_posix().startswith(("venv/", "archive/", "tests/"))
            and (re.search(r"\{\s*10006\s*,", t := p.read_text(encoding="utf-8", errors="ignore")) or "== 10006" in t)]
    assert hits == ["exchange/bybit_client.py"], hits


def test_funding_hours_have_one_source():
    hits = [rel for rel, text in code_files() if re.search(r"\((23, 7, 15|0, 8, 16)\)", text)]
    assert hits == ["time_filter.py"], hits


def test_is_good_time_skips_the_windows_around_funding(monkeypatch):
    import time_filter
    from datetime import UTC, datetime

    class Clock(datetime):
        now_value = None

        @classmethod
        def now(cls, tz=None):
            return cls.now_value
    monkeypatch.setattr(time_filter, "datetime", Clock)
    for hh, mm, ok in ((23, 50, False), (0, 10, False), (0, 20, True), (7, 44, True), (7, 45, False), (16, 15, False), (12, 0, True)):
        Clock.now_value = datetime(2026, 9, 29, hh, mm, tzinfo=UTC)
        assert time_filter.is_good_time() is ok, (hh, mm)


def test_portfolio_capital_and_stop_come_from_the_executor(tmp_path):
    s = Store(str(tmp_path / "p.db"))
    s.set("started_at", 1)
    s.set("start_equity", 1000.0)
    s.set("capital", 2500.0)
    s.snapshot(2, 990.0, {"AUSDT": 250.0})
    s.conn.close()
    p = rd.read_portfolio(str(tmp_path / "p.db"), 1000.0, 3, stop_fraction=0.25)
    assert p["capital"] == 2500.0, "капитал — записанный исполнителем, а не копия в окружении API"
    assert p["positions"][0]["weight"] == pytest.approx(0.1)
    assert p["stop_fraction"] == 0.25


def test_overview_uses_rule_constants():
    from btcalts import engine as be
    from carry import engine as ce
    from portfolio import engine as pe
    prog = rd.program()
    assert "×1,61" in prog["portfolio"]["risk"] and "×0,48" in prog["portfolio"]["risk"]
    assert pe.K_TREND == pytest.approx(1.61)
    assert "{k_trend}" in rd.PROGRAM["portfolio"]["risk"], "шаблон, а не копия числа"
    assert be.MAX_DRAWDOWN == 0.25 and ce.DANGER_MM_RATE == pytest.approx(2 / 3)


def test_btcalts_stop_uses_its_own_rule(monkeypatch, tmp_path):
    """drawdown_confirmed брал порог И14 и для И18; теперь порог передаёт исполнитель из своего правила."""
    import inspect
    from btcalts import __main__ as bm
    from portfolio import executor
    assert "engine.MAX_DRAWDOWN" in inspect.getsource(bm.cycle)
    s = Store(str(tmp_path / "x.db"))
    s.set("peak_equity", 1000.0)

    class Cli:
        def usdt_equity(self):
            return 850.0
    assert executor.drawdown_confirmed(Cli(), s, 1000.0, 1, 0.10) is not None
    assert executor.drawdown_confirmed(Cli(), s, 1000.0, 1, 0.25) is None


def test_stale_files_are_gone():
    for rel in ("miniapp/nginx.conf", "docker-compose.yml", "monitoring/alertmanager.yml"):
        assert not (ROOT / rel).exists(), rel
