"""
Стартовый капитал и пик в реальных режимах (10.09.2026, находка 11).

Раньше в TESTNET/LIVE текущий баланс брался с кошелька, а стартовый капитал и
пик для просадки — от бумажных 100 $: при кошельке в 1000 $ «убыток» Risk Core
и «просадка» выключателя ничего не значили.
"""
import pathlib

import pytest

import capital
import database
from config import INITIAL_BALANCE

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "capital.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


class Wallet:
    def __init__(self, equity):
        self.equity = equity

    def snapshot(self):
        return (self.equity, self.equity)


@pytest.fixture
def real(monkeypatch, db):
    """Режим с реальными ордерами и кошелёк под контролем теста."""
    wallet = Wallet(300.0)
    mode = {"key": "TESTNET"}
    monkeypatch.setattr(capital, "_real_orders_mode", lambda: True)
    monkeypatch.setattr(capital, "_real_mode_key", lambda: mode["key"])
    monkeypatch.setattr(capital, "_wallet_snapshot", wallet.snapshot)
    return wallet, mode


def test_baseline_is_recorded_once_from_the_wallet(real):
    wallet, _ = real
    assert capital.get_initial_balance() == pytest.approx(300.0)
    wallet.equity = 250.0
    assert capital.get_initial_balance() == pytest.approx(300.0), "база не плывёт вместе с equity"


def test_drawdown_is_measured_from_the_highest_equity_seen(real):
    wallet, _ = real
    capital.get_initial_balance()
    wallet.equity = 330.0
    assert capital.current_drawdown_pct() == pytest.approx(0.0)
    wallet.equity = 297.0
    assert capital.current_drawdown_pct() == pytest.approx(10.0), "(330 − 297) / 330"
    assert capital.get_initial_balance() == pytest.approx(300.0)


def test_testnet_and_live_keep_separate_baselines(real):
    wallet, mode = real
    capital.get_initial_balance()
    mode["key"] = "LIVE"
    wallet.equity = 1000.0
    assert capital.get_initial_balance() == pytest.approx(1000.0)
    mode["key"] = "TESTNET"
    assert capital.get_initial_balance() == pytest.approx(300.0)


def test_unreachable_wallet_gives_no_baseline(monkeypatch, db):
    monkeypatch.setattr(capital, "_real_orders_mode", lambda: True)
    monkeypatch.setattr(capital, "_real_mode_key", lambda: "TESTNET")
    monkeypatch.setattr(capital, "_wallet_snapshot", lambda: None)
    assert capital.get_initial_balance() == 0.0
    assert database.get_capital_baseline("TESTNET") is None, "без кошелька базу не выдумываем"


def test_paper_mode_is_unchanged(monkeypatch, db):
    monkeypatch.setattr(capital, "_real_orders_mode", lambda: False)
    assert capital.get_initial_balance() == INITIAL_BALANCE
    assert capital.get_peak_balance() == pytest.approx(INITIAL_BALANCE)


@pytest.mark.parametrize("path", [
    "execution/gatekeeper.py", "telegram_commands.py", "daily_report.py", "bot_statistics.py",
])
def test_consumers_use_the_mode_aware_baseline(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert "INITIAL_BALANCE" not in text, "бумажная константа там, где нужен капитал режима"
    assert "get_initial_balance()" in text
