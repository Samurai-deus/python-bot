SYMBOLS = [
    # Топ-3 по капитализации (максимально стабильные)
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",

    # Популярные альткоины с высокой ликвидностью
    "SOLUSDT",
    "XRPUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "POLUSDT",   # Polygon (бывший MATICUSDT, переименован в 2024)
    "LINKUSDT",

    # DeFi токены
    "UNIUSDT",
    "AAVEUSDT",

    # L2 и новые протоколы
    "ARBUSDT",
    "OPUSDT",
    "SUIUSDT",
    "APTUSDT",

    # Мемкоины с высокой ликвидностью (нестандартные имена на Bybit)
    "SHIB1000USDT",   # SHIB на Bybit — SHIB1000USDT (не 1000SHIBUSDT)
    "1000PEPEUSDT",

    # Дополнительные ликвидные пары
    "ATOMUSDT",
    "NEARUSDT",

    # Новые высоколиквидные токены (2024–2025)
    "INJUSDT",     # Injective
    "WLDUSDT",     # Worldcoin
    "TIAUSDT",     # Celestia
    "RENDERUSDT",  # Render Network
    # FETUSDT отсутствует как linear perp на Bybit (ASI Alliance merger)
    "EIGENUSDT",   # EigenLayer
    "JUPUSDT",     # Jupiter (Solana DEX)
]

TIMEFRAMES = {
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
    "4h": "240"
}

CANDLE_LIMIT = 120

# Параметры управления капиталом — ЕДИНСТВЕННОЕ место. До 10.09.2026 capital.py
# держал собственные копии этих констант, и разные модули читали разные.
from utils.env import env_float as _env_float

# Стартовый баланс бумажного счёта (DRY_RUN и PAPER_TRADING). В TESTNET и LIVE баланс
# берётся из кошелька биржи, это значение там не используется.
INITIAL_BALANCE = _env_float("PAPER_INITIAL_BALANCE_USDT", 100.0)
# Риск на сделку, % от баланса: сколько теряем при срабатывании стопа.
RISK_PERCENT = _env_float("RISK_PERCENT", 2.0)
# Минимальный номинал позиции — минимальный ордер Bybit (5 $ у всех linear-пар
# конфига на 10.09.2026). Точную проверку по лоту и цене делает
# market_data.instrument_limits; это значение — ранний отсев.
MIN_POSITION_SIZE = _env_float("MIN_POSITION_SIZE_USDT", 5.0)
MAX_POSITION_SIZE = _env_float("MAX_POSITION_SIZE_USDT", 1000.0)
POSITION_ALLOCATION_PERCENT = 3.0  # Base % of available capital to allocate per trade (professional: 1-3%)
