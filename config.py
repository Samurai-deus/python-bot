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

    # Мемкоины с высокой ликвидностью
    "SHIBUSDT",
    "PEPEUSDT",

    # Дополнительные ликвидные пары
    "ATOMUSDT",
    "NEARUSDT",

    # Новые высоколиквидные токены (2024–2025)
    "TONUSDT",     # The Open Network
    "INJUSDT",     # Injective
    "WLDUSDT",     # Worldcoin
    "TIAUSDT",     # Celestia
    "RENDERUSDT",  # Render Network
    "FETUSDT",     # Fetch.ai (ASI Alliance)
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

# Параметры управления капиталом
INITIAL_BALANCE = 10000.0  # Начальный баланс в USDT
RISK_PERCENT = 2.0  # Риск на сделку (% от баланса)
MIN_POSITION_SIZE = 10.0  # Минимальный размер позиции в USDT
MAX_POSITION_SIZE = 1000.0  # Максимальный размер позиции в USDT
