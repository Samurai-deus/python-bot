"""
Демо-счёт Bybit (11.09.2026): верификация на testnet не прошла, прогон с
настоящими ордерами без реальных денег — на демо-счёте основного аккаунта
(api-demo.bybit.com). Здесь: режим и хост, потолок капитала (на демо-балансе
десятки тысяч виртуальных USDT, а план — счёт в 100 $) и шаги деплоя.
"""
import re

import pytest

import capital
import config
import database
import trading_mode
from tests.test_deploy_artifacts import DEPLOY, step_body
from trading_mode import TradingMode

MODE_VARS = ("LIVE_TRADING", "PAPER_TRADING", "BYBIT_TESTNET", "BYBIT_DEMO", "DRY_RUN")


@pytest.fixture
def env(monkeypatch):
    for name in MODE_VARS:
        monkeypatch.delenv(name, raising=False)

    def set_flags(**flags):
        for name, value in flags.items():
            monkeypatch.setenv(name, value)
    return set_flags


# ---------------------------------------------------------------------------
# Режим и хост
# ---------------------------------------------------------------------------

def test_demo_flag_sends_real_orders_to_the_demo_host(env):
    env(BYBIT_DEMO="true")
    assert trading_mode.get_trading_mode() == TradingMode.TESTNET
    assert trading_mode.sends_real_orders() is True
    assert trading_mode.uses_demo_endpoint() is True
    assert trading_mode.uses_testnet_endpoint() is False
    assert trading_mode.risks_real_money() is False


@pytest.mark.parametrize("flags, mode", [
    ({"BYBIT_DEMO": "true", "PAPER_TRADING": "true"}, TradingMode.PAPER_TRADING),
    ({"BYBIT_DEMO": "true", "DRY_RUN": "true"}, TradingMode.DRY_RUN),
    ({"BYBIT_DEMO": "true", "LIVE_TRADING": "true"}, TradingMode.LIVE),
])
def test_demo_never_overrides_a_stronger_mode(env, flags, mode):
    env(**flags)
    assert trading_mode.get_trading_mode() == mode
    assert trading_mode.uses_demo_endpoint() is False


@pytest.mark.parametrize("flags, base, environment", [
    ({"BYBIT_DEMO": "true"}, "_DEMO_BASE", "DEMO"),
    ({"BYBIT_DEMO": "true", "BYBIT_TESTNET": "true"}, "_DEMO_BASE", "DEMO"),
    ({"BYBIT_TESTNET": "true"}, "_TESTNET_BASE", "TESTNET"),
    ({"PAPER_TRADING": "true"}, "_MAINNET_BASE", "MAINNET"),
])
def test_client_host_follows_the_mode(env, flags, base, environment):
    from exchange import bybit_client
    env(**flags)
    client = bybit_client.BybitClient(api_key="k", api_secret="s")
    assert client._base_url == getattr(bybit_client, base)
    assert client.environment == environment
    assert client._testnet is (environment == "TESTNET")


def test_demo_host_is_the_bybit_demo_domain():
    from exchange import bybit_client
    assert bybit_client._DEMO_BASE == "https://api-demo.bybit.com"


def test_an_explicit_demo_client_ignores_the_mode(env):
    """Шаг deploy.sh bybit-key проверяет ключ на демо-бирже, пока прод ещё на бумаге."""
    from exchange import bybit_client
    env(PAPER_TRADING="true")
    client = bybit_client.BybitClient(api_key="k", api_secret="s", demo=True)
    assert client._base_url == bybit_client._DEMO_BASE


def test_the_executor_labels_demo_orders(env):
    from exchange import bybit_client
    from execution import order_executor
    env(BYBIT_DEMO="true")
    executor = order_executor.OrderExecutor(client=bybit_client.BybitClient(api_key="k", api_secret="s"),
                                            sleep=lambda s: None)
    assert executor._client.environment == "DEMO"
    source = (DEPLOY.parent / "execution" / "order_executor.py").read_text(encoding="utf-8")
    assert '("LIVE" if env == "MAINNET" else env)' in source


def test_demo_keeps_its_own_capital_baseline(env):
    env(BYBIT_DEMO="true")
    assert capital._real_mode_key() == "DEMO"
    env(BYBIT_DEMO="false", BYBIT_TESTNET="true")
    assert capital._real_mode_key() == "TESTNET"


# ---------------------------------------------------------------------------
# Потолок капитала
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    if getattr(database, "_PG_MODE", False):
        pytest.skip("тест для SQLite")
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "demo.db"))
    monkeypatch.setattr(database._thread_local, "conn", None, raising=False)
    yield database
    conn = getattr(database._thread_local, "conn", None)
    if conn is not None:
        conn.close()
    database._thread_local.conn = None


class Wallet:
    def __init__(self, equity, available=None):
        self.equity = equity
        self.available = equity if available is None else available

    def snapshot(self):
        return (self.equity, self.available)


@pytest.fixture
def demo_wallet(monkeypatch, db):
    """Демо-кошелёк на 50 000 виртуальных USDT и потолок 100 $."""
    wallet = Wallet(50_000.0)
    monkeypatch.setattr(capital, "_real_orders_mode", lambda: True)
    monkeypatch.setattr(capital, "_real_mode_key", lambda: "DEMO")
    monkeypatch.setattr(capital, "_wallet_snapshot", wallet.snapshot)
    monkeypatch.setattr(capital, "_capital_cap", lambda: 100.0)
    return wallet


def test_a_demo_wallet_is_seen_as_a_100_dollar_account(demo_wallet):
    assert capital.get_initial_balance() == pytest.approx(100.0)
    assert capital.get_current_balance() == pytest.approx(100.0)
    assert capital.get_available_capital() == pytest.approx(100.0)
    assert database.get_capital_baseline("DEMO")["initial"] == pytest.approx(50_000.0), "в базе — сырые значения"


def test_losses_and_margin_show_up_in_the_capped_view(demo_wallet):
    capital.get_initial_balance()
    demo_wallet.equity, demo_wallet.available = 49_990.0, 49_960.0  # убыток 10 $, маржа 30 $
    assert capital.get_current_balance() == pytest.approx(90.0)
    assert capital.get_available_capital() == pytest.approx(60.0)
    assert capital.current_drawdown_pct() == pytest.approx(10.0), "просадка от счёта в 100 $, а не от 50 000"


def test_profit_raises_the_peak_in_the_capped_view(demo_wallet):
    capital.get_initial_balance()
    demo_wallet.equity = demo_wallet.available = 50_020.0
    assert capital.get_peak_balance() == pytest.approx(120.0)
    demo_wallet.equity = demo_wallet.available = 50_008.0
    assert capital.current_drawdown_pct() == pytest.approx(10.0), "(120 − 108) / 120"


def test_a_position_can_never_use_more_than_the_cap(demo_wallet):
    capital.get_initial_balance()
    demo_wallet.available = 20_000.0  # на демо занято 30 000 $ маржи — больше потолка
    assert capital.get_available_capital() == 0.0


def test_without_a_cap_the_wallet_is_used_as_is(monkeypatch, db):
    wallet = Wallet(300.0)
    monkeypatch.setattr(capital, "_real_orders_mode", lambda: True)
    monkeypatch.setattr(capital, "_real_mode_key", lambda: "TESTNET")
    monkeypatch.setattr(capital, "_wallet_snapshot", wallet.snapshot)
    monkeypatch.setattr(capital, "_capital_cap", lambda: 0.0)
    assert capital.get_initial_balance() == pytest.approx(300.0)
    wallet.equity = wallet.available = 330.0
    assert capital.get_current_balance() == pytest.approx(330.0)
    assert capital.get_peak_balance() == pytest.approx(330.0)


def test_margin_counts_even_when_other_coins_inflate_the_free_balance(demo_wallet, monkeypatch):
    """
    Настоящий демо-кошелёк 11.09.2026: в залоге USDT, USDC, BTC и ETH, свободный
    остаток счёта (все монеты, USD) — 1,97 млн при equity по USDT 150 000.
    Разность equity − available всегда 0, маржа берётся из журнала сделок.
    """
    capital.get_initial_balance()
    demo_wallet.available = 1_975_738.76
    monkeypatch.setattr(capital, "get_open_margin", lambda: 30.0)
    assert capital.get_current_balance() == pytest.approx(100.0)
    assert capital.get_available_capital() == pytest.approx(70.0)


def test_open_trades_are_told_apart_by_where_they_live(db):
    database.add_trade("SOLUSDT", "LONG", 100.0, 97.0, 106.0, position_size=30.0, leverage=5.0)
    database.add_trade("ETHUSDT", "SHORT", 3000.0, 3100.0, 2800.0, position_size=30.0, leverage=5.0,
                       exchange_order_id="ord-1")
    database.add_trade("BTCUSDT", "LONG", 60000.0, 0.0, 0.0, position_size=30.0, leverage=5.0,
                       strategy_name="adopted_from_exchange")
    assert database.count_open_trades(on_exchange=False) == 1, "бумажная — без ордера биржи"
    assert database.count_open_trades(on_exchange=True) == 2, "с ордером биржи и взятая с биржи при сверке"


@pytest.mark.parametrize("value, cap", [(100.0, 100.0), (0.0, 0.0), (-5.0, 0.0)])
def test_the_cap_comes_from_config(monkeypatch, value, cap):
    monkeypatch.setattr(config, "REAL_CAPITAL_CAP_USDT", value)
    assert capital._capital_cap() == cap


# ---------------------------------------------------------------------------
# Шаги деплоя
# ---------------------------------------------------------------------------

SCRIPT = (DEPLOY / "deploy.sh").read_text(encoding="utf-8")


def test_bybit_key_step_checks_the_demo_exchange_before_editing_env_and_hides_the_key():
    step = step_body(SCRIPT, "step_bybit_key")
    assert step.index("demo=True") < step.index('cp -p "$APP/.env"') < step.index('mv -f "$APP/.env.new" "$APP/.env"')
    assert "docker exec -i market-bot python" in step and "sys.stdin.read()" in step, "ключ — через stdin"
    assert 'ENVIRON["BYBIT_KEY"]' in step and 'ENVIRON["BYBIT_SECRET"]' in step and "-v " not in step
    assert "--force-recreate" not in step, "ключи не меняют режим — перезапуск делает шаг mode"
    secret_var = re.compile(r"\$\{?(key|secret)(?![A-Za-z0-9_])")
    for line in step.splitlines():
        if line.strip().startswith("echo"):
            assert not secret_var.search(line), f"ключ уходит в вывод: {line.strip()}"
    assert "bybit-key) step_bybit_key" in SCRIPT


def test_mode_step_refuses_demo_without_keys_and_records_the_expected_mode():
    step = step_body(SCRIPT, "step_mode")
    assert step.index("^BYBIT_API_KEY=") < step.index("--force-recreate"), "без ключа в демо не переключаемся"
    assert "BYBIT_DEMO=true REAL_CAPITAL_CAP_USDT=100" in step and "PAPER_TRADING=false" in step
    assert "PAPER_TRADING=true" in step and "BYBIT_DEMO=false" in step, "обратный путь — mode paper"
    assert "trading_mode.expected" in step
    assert 'mode)    step_mode "$@"' in SCRIPT
    guard = step.index("count_open_trades(on_exchange=$others)")
    assert guard < step.index('cp -p "$APP/.env"'), "журнал проверяется до правки .env"
    assert "expected=TESTNET; others=False" in step and "expected=PAPER_TRADING; others=True" in step
    smoke = step_body(SCRIPT, "step_smoke")
    assert "trading_mode.expected" in smoke and '"$mode" = "$expected"' in smoke
