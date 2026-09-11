"""
Ф0 плана трейдера (docs/TRADER_PLAN.md): метка версии и режима на каждой записи.

11.09.2026 размер позиции и блокеры менялись 6–7 раз за день, а у сделок, сигналов и
мнений ИИ не было метки версии — недельный отчёт смешивал результаты разных «ботов».
Теперь у каждой записи «коммит релиза-хеш торговых настроек», у сделки — режим, а отчёт
считает только текущую версию.
"""
import pathlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import config
import database
import journal
from core import release

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "versioning.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    journal._recent.clear()
    release.version.cache_clear()
    yield database
    journal._recent.clear()
    release.version.cache_clear()
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


def query(sql, params=()):
    conn = database.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(sql, params)
        rows = [dict(r) for r in cursor.fetchall()] if sql.lstrip().upper().startswith("SELECT") else []
        conn.commit()
        return rows
    finally:
        conn.close()


def test_the_release_file_is_filled_by_git_archive():
    assert (ROOT / "release_commit.txt").read_text(encoding="utf-8").strip() == "$Format:%H$"
    assert "release_commit.txt export-subst" in (ROOT / ".gitattributes").read_text(encoding="utf-8")


def test_a_working_copy_is_dev_and_a_release_is_its_commit(tmp_path, monkeypatch):
    stamp = tmp_path / "release_commit.txt"
    monkeypatch.setattr(release, "RELEASE_FILE", stamp)
    stamp.write_text("$Format:%H$\n", encoding="utf-8")
    assert release.release_commit() == "dev"
    stamp.write_text("0123456789abcdef0123\n", encoding="utf-8")
    assert release.release_commit() == "0123456789ab"
    monkeypatch.setattr(release, "RELEASE_FILE", tmp_path / "missing.txt")
    assert release.release_commit() == "dev"


def test_a_trading_setting_changes_the_version(monkeypatch):
    before = release.settings_hash()
    monkeypatch.setattr(config, "RISK_PERCENT", config.RISK_PERCENT + 1)
    assert release.settings_hash() != before


def test_a_trade_records_its_version_and_mode(db, monkeypatch):
    import trading_mode
    monkeypatch.setattr(trading_mode, "get_trading_mode", lambda: trading_mode.TradingMode.TESTNET)
    monkeypatch.setattr(trading_mode, "uses_demo_endpoint", lambda: True)
    trade_id = database.add_trade("ADAUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0, exchange_order_id="o-1")
    row, = query("SELECT version, mode FROM trades WHERE id = ?", (trade_id,))
    assert row == {"version": release.version(), "mode": "DEMO"}


def test_the_exchange_filter_knows_the_mode_column(db):
    demo = database.add_trade("ADAUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0)
    paper = database.add_trade("DOTUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0)
    legacy = database.add_trade("ARBUSDT", "SHORT", 1.0, 1.1, 0.9, position_size=10.0, exchange_order_id="o-2")
    query("UPDATE trades SET mode = 'DEMO' WHERE id = ?", (demo,))
    query("UPDATE trades SET mode = 'PAPER' WHERE id = ?", (paper,))
    query("UPDATE trades SET mode = NULL WHERE id = ?", (legacy,))
    assert database.count_open_trades(on_exchange=True) == 2, "демо по колонке и старая строка по ордеру"
    assert database.count_open_trades(on_exchange=False) == 1


def test_journal_and_ai_opinions_carry_the_version(db):
    journal.record_signal(symbol="ADAUSDT", side="LONG", entry=100.0, stop=98.0, target=104.0, status=journal.SENT)
    assert database.get_signals_from_db("1970-01-01")[0]["version"] == release.version()
    database.save_ai_opinion(signal_ts="2026-09-11T10:00:00+00:00", symbol="ADAUSDT", side="LONG", stage="0",
                             model="m", decision="approve", size_multiplier=1.0, confidence=0.6, reasons=[],
                             key_risk="", cost_usd=0.0, latency_ms=1, error=None)
    assert len(database.get_ai_verdicts("1970-01-01", version=release.version())) == 1
    assert database.get_ai_verdicts("1970-01-01", version="другая-версия") == []


def test_old_tables_get_the_version_columns(tmp_path):
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.execute("CREATE TABLE ai_opinions (signal_ts TEXT NOT NULL, symbol TEXT NOT NULL)")
    conn.execute("CREATE TABLE signal_journal (id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT)")
    database._ensure_ai_tables(conn.cursor())
    database._ensure_signal_journal_columns(conn.cursor())
    database._ensure_signal_journal_columns(conn.cursor())  # повторно — без ошибок
    for table in ("ai_opinions", "signal_journal"):
        assert "version" in {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}, table
    conn.close()


def test_the_weekly_report_counts_only_the_current_version(db, monkeypatch):
    import capital
    import trading_mode
    from analytics import weekly_report
    monkeypatch.setattr(trading_mode, "sends_real_orders", lambda: True)
    monkeypatch.setattr(capital, "get_current_balance", lambda: 1000.0)
    current = database.add_trade("ADAUSDT", "LONG", 100.0, 98.0, 104.0, position_size=50.0,
                                 strategy_name="trend_following", exchange_order_id="a")
    old = database.add_trade("DOTUSDT", "LONG", 100.0, 98.0, 104.0, position_size=50.0,
                             strategy_name="breakout", exchange_order_id="b")
    database.close_trade(current, 101.0, "EXCHANGE_CLOSE", 2.0)
    database.close_trade(old, 101.0, "EXCHANGE_CLOSE", 5.0)
    query("UPDATE trades SET version = 'old-00000000' WHERE id = ?", (old,))
    report = weekly_report.build_weekly_report()
    lines = report.splitlines()
    assert release.version() in lines[0]
    assert "Другие версии за неделю (в отчёт не вошли): 1 сд., 0 сигналов." in lines
    assert "trend_following" in report and "breakout" not in report
