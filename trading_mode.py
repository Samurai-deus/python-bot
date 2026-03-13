"""
Trading mode resolver — Phase 2.

Единственное место, где читаются DRY_RUN / PAPER_TRADING / LIVE_TRADING из .env.
Все модули импортируют get_trading_mode() вместо прямого чтения os.environ.
"""
import os
from enum import Enum


class TradingMode(str, Enum):
    DRY_RUN = "DRY_RUN"             # Сигналы в Telegram, биржа не вызывается
    PAPER_TRADING = "PAPER_TRADING"  # Виртуальные сделки, биржа не вызывается
    TESTNET = "TESTNET"              # Реальные ордера на Bybit Testnet
    LIVE = "LIVE"                    # Реальные ордера на Bybit Mainnet (Phase 6+)


def get_trading_mode() -> TradingMode:
    """
    Определить активный режим торговли из .env.

    Приоритет (первый True побеждает):
    1. LIVE_TRADING=true  → LIVE
    2. PAPER_TRADING=true → PAPER_TRADING
    3. BYBIT_TESTNET=true + DRY_RUN=false → TESTNET
    4. иначе              → DRY_RUN (безопасный дефолт)
    """
    def _flag(name: str) -> bool:
        return os.environ.get(name, "false").lower() == "true"

    if _flag("LIVE_TRADING"):
        return TradingMode.LIVE
    if _flag("PAPER_TRADING"):
        return TradingMode.PAPER_TRADING
    if _flag("BYBIT_TESTNET") and not _flag("DRY_RUN"):
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
