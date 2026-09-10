"""
Бумажный учёт на 100 $ (аудит 10.09.2026): комиссии в PnL и честный ноль.

Раньше бумажный PnL был без комиссий, а баланс не опускался ниже 10 $, и
risk_exposure_brain при нулевом балансе подставлял начальный. Слитый счёт
выглядел живым, а сделки — выгоднее реальных.
"""
import pytest

import trade_manager


def trade(side="LONG", entry=100.0, size=50.0, partial_closed=False):
    return {"side": side, "entry": entry, "position_size": size, "partial_closed": partial_closed}


FEE_BOTH_SIDES_ON_50 = 50.0 * 0.00055 * 2  # 0,055


@pytest.fixture(autouse=True)
def taker_fee(monkeypatch):
    monkeypatch.setenv("PAPER_TAKER_FEE_PCT", "0.055")


# ---------------------------------------------------------------------------
# Комиссии
# ---------------------------------------------------------------------------

def test_flat_trade_loses_exactly_the_fees():
    """Вход и выход по одной цене — на реальной бирже это минус две комиссии, а не ноль."""
    assert trade_manager._calc_pnl(trade(), 100.0) == pytest.approx(-FEE_BOTH_SIDES_ON_50)


def test_long_profit_is_net_of_fees():
    # +2 % на 50 $ = 1,00 $ до комиссий
    assert trade_manager._calc_pnl(trade(), 102.0) == pytest.approx(1.0 - FEE_BOTH_SIDES_ON_50)


def test_short_profit_is_net_of_fees():
    assert trade_manager._calc_pnl(trade(side="SHORT"), 98.0) == pytest.approx(1.0 - FEE_BOTH_SIDES_ON_50)


def test_loss_grows_by_fees():
    assert trade_manager._calc_pnl(trade(), 98.0) == pytest.approx(-1.0 - FEE_BOTH_SIDES_ON_50)


def test_partial_close_pays_fees_for_its_share_only():
    pnl = trade_manager._calc_pnl(trade(), 102.0, fraction=0.5)
    assert pnl == pytest.approx(0.5 - FEE_BOTH_SIDES_ON_50 / 2)


def test_zero_fee_setting_gives_gross(monkeypatch):
    monkeypatch.setenv("PAPER_TAKER_FEE_PCT", "0")
    assert trade_manager._calc_pnl(trade(), 102.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Слитый счёт
# ---------------------------------------------------------------------------

class _Cursor:
    def __init__(self, total_pnl):
        self._total = total_pnl

    def execute(self, *args):
        pass

    def fetchone(self):
        # второй запрос баланса — частичный PnL открытых сделок
        return {"total_pnl": self._total, "partial": 0.0}


class _Conn:
    def __init__(self, total_pnl):
        self._total = total_pnl

    def cursor(self):
        return _Cursor(self._total)

    def close(self):
        pass


def test_blown_account_balance_is_zero_not_ten(monkeypatch):
    """Счёт 100 $ с убытком 150 $ — это 0 $, а не 10 $ несуществующих денег."""
    import database
    monkeypatch.setattr(database, "get_db_connection", lambda: _Conn(-150.0))
    assert database.get_current_balance_from_db(100.0) == 0.0


def test_healthy_account_balance_is_unchanged(monkeypatch):
    import database
    monkeypatch.setattr(database, "get_db_connection", lambda: _Conn(12.5))
    assert database.get_current_balance_from_db(100.0) == pytest.approx(112.5)


def test_empty_account_reads_as_fully_exposed(monkeypatch):
    """
    Раньше при нулевом балансе risk_exposure_brain подставлял начальный — и
    позиция в 50 $ выглядела как 50 % экспозиции здорового счёта.
    """
    import brains.risk_exposure_brain as reb
    monkeypatch.setattr(reb, "get_current_balance", lambda: 0.0)
    brain = reb.get_risk_exposure_brain()
    open_trades = [{"entry": 100.0, "stop": 98.0, "position_size": 50.0, "side": "LONG"}]
    assert brain._calculate_exposure(open_trades) == 100.0
