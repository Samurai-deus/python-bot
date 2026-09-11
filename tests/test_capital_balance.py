"""
Откуда берётся баланс и как считается размер (аудит 10.09.2026, блокер B-4).

Раньше и в режимах с реальными ордерами размер и лимиты риска считались от
«стартовый баланс + PnL бумажной таблицы»: при реальном кошельке в 300 $ —
от 10 000 $. Кошелёк в торговом пути не спрашивался вовсе.
"""
from types import SimpleNamespace

import pytest

import capital


@pytest.fixture(autouse=True)
def fresh_wallet_cache():
    capital._clear_wallet_cache()
    yield
    capital._clear_wallet_cache()


@pytest.fixture
def testnet(monkeypatch):
    for name in ("LIVE_TRADING", "PAPER_TRADING"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BYBIT_TESTNET", "true")
    monkeypatch.setenv("DRY_RUN", "false")


@pytest.fixture
def paper(monkeypatch):
    for name in ("LIVE_TRADING", "BYBIT_TESTNET"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_TRADING", "true")


def wallet(equity, available, calls=None):
    def get_wallet_balance():
        if calls is not None:
            calls.append(1)
        return SimpleNamespace(total_equity=equity, available_balance=available)
    return SimpleNamespace(get_wallet_balance=get_wallet_balance)


def must_not_be_called(*args, **kwargs):
    pytest.fail("этот источник баланса в этом режиме читаться не должен")


# ---------------------------------------------------------------------------
# Источник баланса
# ---------------------------------------------------------------------------

def test_real_orders_mode_reads_the_wallet(testnet, monkeypatch):
    monkeypatch.setattr(capital, "_get_wallet_client", lambda: wallet(300.0, 280.0))
    monkeypatch.setattr(capital, "get_current_balance_from_db", must_not_be_called)
    assert capital.get_current_balance() == 300.0
    assert capital.get_available_capital() == 280.0


def test_unreachable_wallet_means_zero_not_a_fallback(testnet, monkeypatch):
    """Не можем узнать, сколько денег, — значит, позиций не открываем."""
    def broken():
        raise ConnectionError("bybit down")

    monkeypatch.setattr(capital, "_get_wallet_client", lambda: SimpleNamespace(get_wallet_balance=broken))
    monkeypatch.setattr(capital, "get_current_balance_from_db", must_not_be_called)
    assert capital.get_current_balance() == 0.0
    assert capital.get_available_capital() == 0.0
    assert capital.position_size(100.0, 98.0) == 0.0


def test_wallet_is_asked_at_most_once_per_cache_window(testnet, monkeypatch):
    calls = []
    monkeypatch.setattr(capital, "_get_wallet_client", lambda: wallet(300.0, 280.0, calls))
    capital.get_current_balance()
    capital.get_available_capital()
    assert calls == [1]


def test_paper_mode_never_asks_the_exchange(paper, monkeypatch):
    monkeypatch.setattr(capital, "_get_wallet_client", must_not_be_called)
    monkeypatch.setattr(capital, "get_current_balance_from_db", lambda initial: initial + 12.5)
    assert capital.get_current_balance() == pytest.approx(capital.INITIAL_BALANCE + 12.5)


def test_capital_constants_have_a_single_source():
    """Раньше capital.py держал свои копии констант config.py — теперь это те же объекты."""
    import config
    assert capital.INITIAL_BALANCE == config.INITIAL_BALANCE
    assert capital.MIN_POSITION_SIZE == config.MIN_POSITION_SIZE
    assert capital.RISK_PERCENT == config.RISK_PERCENT


# ---------------------------------------------------------------------------
# Размер из риска
# ---------------------------------------------------------------------------

@pytest.fixture
def paper_100(paper, monkeypatch):
    monkeypatch.setattr(capital, "get_current_balance_from_db", lambda initial: 100.0)
    monkeypatch.setattr(capital, "get_total_open_positions_size", lambda: 0.0)
    monkeypatch.setattr(capital, "get_open_margin", lambda: 0.0)
    monkeypatch.setattr(capital, "get_open_risk_usd", lambda: 0.0)
    monkeypatch.setattr(capital, "get_open_trades", lambda: [])
    import core.risk_core as risk_core_module
    monkeypatch.setattr(risk_core_module, "_risk_core", None)  # пределы процесса — из config.py
    monkeypatch.setattr(capital, "RISK_PERCENT", 2.0)
    monkeypatch.setattr(capital, "MIN_POSITION_SIZE", 5.0)
    monkeypatch.setattr(capital, "MAX_POSITION_SIZE", 1000.0)


def test_small_position_is_skipped_not_inflated(paper_100):
    """
    Риск 2 $ при стопе в 50 % даёт номинал 4 $ — ниже минимума 5 $. Раньше
    max(MIN_POSITION_SIZE, ...) раздувал такую позицию до минимума, и риск на
    сделке превышался. Теперь позиция не открывается.
    """
    assert capital.position_size(100.0, 50.0) == 0.0


def test_loss_at_stop_equals_risk_when_uncapped(paper_100):
    # риск 2 $, стоп 25 % → номинал 8 $ (меньше предела позиции 10 $);
    # при срабатывании стопа теряем 8 × 25 % = 2 $ — ровно риск
    size = capital.position_size(100.0, 75.0)
    assert size == pytest.approx(8.0)
    assert size * 0.25 == pytest.approx(2.0)


def test_position_is_capped_by_risk_core_single_position_limit(paper_100):
    """
    Риск 2 $ при стопе 1 % дал бы 200 $ номинала. Раньше предел был «весь
    доступный капитал» (100 $), и Risk Core отклонял такую позицию: 100 % баланса
    против лимита 10 %. На проде 10.09.2026 так было отклонено 10 сигналов из 10.
    Теперь предел — тот же процент, которым позицию проверит Risk Core, с запасом
    1 % (RISK_LIMIT_HEADROOM). С 11.09.2026 предел одной позиции — 100 % капитала по
    номиналу (позиции с плечом, шаг 2 плана обучения): 100 $ × 0,99.
    """
    assert capital.position_size(100.0, 99.0) == pytest.approx(99.0)


def test_position_fits_remaining_aggregate_exposure(paper_100, monkeypatch):
    # открыто на 250 $ из допустимых 300 % от 100 $ с запасом 1 % (297 $) → остаётся 47 $
    monkeypatch.setattr(capital, "get_total_open_positions_size", lambda: 250.0)
    assert capital.position_size(100.0, 99.0) == pytest.approx(47.0)


def test_no_position_when_aggregate_room_is_below_minimum(paper_100, monkeypatch):
    # открыто на 294 $ → остаётся 3 $, меньше минимального ордера 5 $
    monkeypatch.setattr(capital, "get_total_open_positions_size", lambda: 294.0)
    assert capital.position_size(100.0, 99.0) == 0.0


@pytest.mark.parametrize("stop", [99.0, 98.5, 95.0, 90.0, 75.0])
def test_sized_position_passes_risk_core_exposure_invariants(paper_100, stop):
    """
    Сквозная согласованность: размер, посчитанный capital, не нарушает
    инварианты экспозиции Risk Core при том же балансе. Именно это расхождение
    обнуляло бумажную торговлю на проде.
    """
    from types import SimpleNamespace
    from core.risk_core import RiskCore, TradingIntent, ViolationReport, config_from_settings

    size = capital.position_size(100.0, stop)
    assert size > 0
    intent = TradingIntent(symbol="ADAUSDT", side="LONG", position_size_usd=size, entry_price=100.0, stop_price=stop)
    report = ViolationReport()
    RiskCore(config_from_settings())._check_exposure_invariants(
        intent,
        SimpleNamespace(total_exposure_usd=0.0, correlation_groups={}, open_positions=[]),
        SimpleNamespace(current_balance_usd=100.0),
        report,
    )
    assert report.violations == []


@pytest.mark.parametrize("open_usd", [0.0, 40.0])
def test_a_small_balance_drift_does_not_trip_risk_core(paper_100, monkeypatch, open_usd):
    """
    Демо-счёт 11.09.2026: размер считался ровно в 10 % от баланса в одну секунду, а
    Risk Core пересчитывал долю от баланса через мгновение — кэш кошелька, живой
    нереализованный PnL. 10,003 $ при балансе 99,98 $ — это 10,005 % > 10 % →
    LIMITED → размер вдвое → ниже минимального ордера биржи; после первой сделки
    отсекались все сигналы. С запасом 1 % дрейф в полпроцента нарушений не даёт.
    """
    from core.risk_core import RiskCore, TradingIntent, ViolationReport, config_from_settings

    monkeypatch.setattr(capital, "get_current_balance_from_db", lambda initial: 100.03)
    monkeypatch.setattr(capital, "get_total_open_positions_size", lambda: open_usd)
    size = capital.position_size(100.0, 99.0)
    assert size > 0
    intent = TradingIntent(symbol="ADAUSDT", side="LONG", position_size_usd=size, entry_price=100.0, stop_price=99.0)
    report = ViolationReport()
    RiskCore(config_from_settings())._check_exposure_invariants(
        intent,
        SimpleNamespace(total_exposure_usd=open_usd, correlation_groups={}, open_positions=[]),
        SimpleNamespace(current_balance_usd=100.03 * 0.995),
        report,
    )
    assert report.violations == [], report.violations


# ---------------------------------------------------------------------------
# Шаг 2 плана обучения (11.09.2026): риск от капитала, остаток суммарного риска
# ---------------------------------------------------------------------------

def test_risk_is_taken_from_equity_not_from_the_free_balance(paper_100, monkeypatch):
    """При занятой марже риск сделки — 2 % всего капитала, а не свободного остатка."""
    monkeypatch.setattr(capital, "get_open_margin", lambda: 50.0)
    assert capital.position_size(100.0, 75.0) == pytest.approx(8.0)  # 2 $ риска / 25 %


def test_size_fits_the_remaining_open_risk(paper_100, monkeypatch):
    # открытые сделки уже рискуют 5,5 $; предел 6 % × 0,99 = 5,94 $ → на новую 0,44 $
    monkeypatch.setattr(capital, "get_open_risk_usd", lambda: 5.5)
    assert capital.position_size(100.0, 95.0) == pytest.approx(8.8)  # 0,44 $ / 5 %


def test_no_position_when_the_portfolio_is_full(paper_100, monkeypatch):
    monkeypatch.setattr(capital, "get_open_trades", lambda: [{}] * 6)
    assert capital.position_size(100.0, 95.0) == 0.0
