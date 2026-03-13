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
    "MATICUSDT",
    "LINKUSDT",
    
    # DeFi токены (стабильные)
    "UNIUSDT",
    "AAVEUSDT",
    "MKRUSDT",
    
    # L2 и новые протоколы
    "ARBUSDT",
    "OPUSDT",
    "SUIUSDT",
    "APTUSDT",
    
    # Мемкоины с высокой ликвидностью
    "SHIBUSDT",
    
    # Дополнительные стабильные пары
    "ATOMUSDT",
    "NEARUSDT",
    "FTMUSDT",
    "ALGOUSDT"
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
