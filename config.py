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
# Потолок капитала реальных режимов (0 — нет): кошелёк виден как счёт такого
# размера с настоящими прибылью и убытком. Для демо-счёта Bybit, где на балансе
# десятки тысяч виртуальных USDT, а прогон идёт по плану счёта в 100 $.
REAL_CAPITAL_CAP_USDT = _env_float("REAL_CAPITAL_CAP_USDT", 0.0)
# Риск на сделку, % от баланса: сколько теряем при срабатывании стопа.
RISK_PERCENT = _env_float("RISK_PERCENT", 2.0)
# Минимальный номинал позиции — минимальный ордер Bybit (5 $ у всех linear-пар
# конфига на 10.09.2026). Точную проверку по лоту и цене делает
# market_data.instrument_limits; это значение — ранний отсев.
MIN_POSITION_SIZE = _env_float("MIN_POSITION_SIZE_USDT", 5.0)
MAX_POSITION_SIZE = _env_float("MAX_POSITION_SIZE_USDT", 1000.0)
# Пределы Risk Core процесса (шаг 2 плана обучения на демо-счёте, 11.09.2026). Размер
# сделки задаёт риск (RISK_PERCENT от капитала до стопа), портфель ограничен числом
# позиций и суммарным риском. Номинальные пределы — под позиции с плечом: при риске
# 1 % и стопе 2 % номинал сделки — половина капитала. Прежние 10/50/30 % номинала при
# 100 $ давали сделки по 10 $ и отказ почти каждому следующему сигналу.
RISK_MAX_SINGLE_POSITION_PCT = _env_float("RISK_MAX_SINGLE_POSITION_PCT", 100.0)
RISK_MAX_AGGREGATE_EXPOSURE_PCT = _env_float("RISK_MAX_AGGREGATE_EXPOSURE_PCT", 300.0)
RISK_MAX_CORRELATED_GROUP_PCT = _env_float("RISK_MAX_CORRELATED_GROUP_PCT", 150.0)
RISK_MAX_OPEN_POSITIONS = int(_env_float("RISK_MAX_OPEN_POSITIONS", 6))
RISK_MAX_OPEN_RISK_PCT = _env_float("RISK_MAX_OPEN_RISK_PCT", 6.0)
RISK_MAX_GROUP_RISK_PCT = _env_float("RISK_MAX_GROUP_RISK_PCT", 3.0)
# Поведенческие пределы Risk Core (шаг 2б, 11.09.2026). Пауза 60 с между действиями
# вместе с урезанием вдвое оставляла на счёте в 100 $ одну сделку за оборот анализа;
# вместо паузы — не больше MAX_NEW_POSITIONS_PER_TURN новых позиций за оборот (≈ 5 мин).
RISK_MAX_ACTIONS_PER_HOUR = int(_env_float("RISK_MAX_ACTIONS_PER_HOUR", 12))
RISK_MAX_ACTIONS_PER_24H = int(_env_float("RISK_MAX_ACTIONS_PER_24H", 100))
RISK_ACTION_COOLDOWN_SECONDS = int(_env_float("RISK_ACTION_COOLDOWN_SECONDS", 0))
MAX_NEW_POSITIONS_PER_TURN = int(_env_float("MAX_NEW_POSITIONS_PER_TURN", 3))
# Серия убытков для паузы Risk Core — в событиях: убытки, закрытые в пределах этого окна
# от самого нового убытка события, — одна ставка (11.09.2026: пять SHORT по альтам
# выбило одним отскоком за 14 минут, и это засчитали как пять убытков подряд).
RISK_LOSS_EVENT_WINDOW_MINUTES = _env_float("RISK_LOSS_EVENT_WINDOW_MINUTES", 15)
POSITION_ALLOCATION_PERCENT = 3.0  # Base % of available capital to allocate per trade (professional: 1-3%)
