"""
Режим торговли — единственный источник истины.

Здесь и только здесь читаются LIVE_TRADING / PAPER_TRADING / BYBIT_TESTNET / BYBIT_DEMO / DRY_RUN.
Остальные модули спрашивают режим функциями ниже и НЕ обращаются к os.environ за
этими именами. За соблюдением следит гейт в CI.

Почему это важно (аудит 10.09.2026, блокер B-2): раньше те же переменные читались
ещё в двух местах и по другому правилу — `value.lower() == "true"` вместо списка
допустимых значений. При `BYBIT_TESTNET=1` режим определялся как TESTNET, гейткипер
пропускал ордер как тестовый, в Telegram уходила пометка [TESTNET], а клиент биржи
не признавал "1" за истину и отправлял ордер на https://api.bybit.com. Разбор
значений вынесен в utils.env.env_flag, где перечислены обе стороны явно.

Два вопроса, на которые отвечает этот модуль, и их важно не смешивать:

  sends_real_orders()     — уходит ли ордер на биржу вообще
  uses_testnet_endpoint() — на какой хост биржи мы при этом смотрим

В DRY_RUN и PAPER_TRADING рыночные данные читаются с mainnet (нужны настоящие цены),
но ордера не отправляются. В TESTNET и то и другое идёт на testnet-хост.
"""
from enum import Enum

from utils.env import env_flag


class TradingMode(str, Enum):
    DRY_RUN = "DRY_RUN"              # Сигналы в Telegram, биржа не вызывается
    PAPER_TRADING = "PAPER_TRADING"  # Виртуальные сделки, биржа не вызывается
    TESTNET = "TESTNET"              # Реальные ордера на Bybit Testnet или демо-счёт (BYBIT_DEMO)
    LIVE = "LIVE"                    # Реальные ордера на Bybit Mainnet


def get_trading_mode() -> TradingMode:
    """
    Активный режим торговли.

    Приоритет (первый сработавший побеждает):
      1. LIVE_TRADING=true                   → LIVE
      2. PAPER_TRADING=true                  → PAPER_TRADING
      3. BYBIT_TESTNET=true или BYBIT_DEMO=true, и DRY_RUN≠true → TESTNET
         (BYBIT_DEMO — демо-счёт основного аккаунта, api-demo.bybit.com)
      4. иначе                               → DRY_RUN

    DRY_RUN — безопасный дефолт: при пустом окружении ордера не уходят никуда.
    """
    if env_flag("LIVE_TRADING"):
        return TradingMode.LIVE
    if env_flag("PAPER_TRADING"):
        return TradingMode.PAPER_TRADING
    if (env_flag("BYBIT_TESTNET") or env_flag("BYBIT_DEMO")) and not env_flag("DRY_RUN"):
        return TradingMode.TESTNET
    return TradingMode.DRY_RUN


def is_dry_run() -> bool:
    return get_trading_mode() == TradingMode.DRY_RUN


def is_paper_trading() -> bool:
    return get_trading_mode() == TradingMode.PAPER_TRADING


def is_testnet() -> bool:
    return get_trading_mode() == TradingMode.TESTNET


def is_live() -> bool:
    return get_trading_mode() == TradingMode.LIVE


def sends_real_orders() -> bool:
    """
    Уходит ли ордер на биржу. Единственный предикат, по которому исполнитель
    решает «отправлять или нет» — вместо собственного чтения DRY_RUN.
    """
    return get_trading_mode() in (TradingMode.TESTNET, TradingMode.LIVE)


def uses_testnet_endpoint() -> bool:
    """
    Смотреть ли на testnet-хост биржи. Только режим TESTNET без BYBIT_DEMO: в
    DRY_RUN и PAPER_TRADING нужны настоящие рыночные данные с mainnet.
    """
    return get_trading_mode() == TradingMode.TESTNET and not env_flag("BYBIT_DEMO")


def uses_demo_endpoint() -> bool:
    """
    Смотреть ли на демо-хост (api-demo.bybit.com): режим TESTNET с BYBIT_DEMO.
    Демо-счёт живёт на основном аккаунте отдельно от реальных денег, цены и
    стакан — основной биржи (11.09.2026: верификация на testnet не прошла).
    Более сильные режимы (LIVE, PAPER_TRADING, DRY_RUN) демо не включают.
    """
    return get_trading_mode() == TradingMode.TESTNET and env_flag("BYBIT_DEMO")


def risks_real_money() -> bool:
    """Ставит ли текущий режим под удар настоящие деньги. Для алертов и баннеров."""
    return get_trading_mode() == TradingMode.LIVE
