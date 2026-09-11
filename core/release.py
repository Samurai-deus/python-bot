"""
Версия торгового поведения для данных (Ф0 плана трейдера, docs/TRADER_PLAN.md).

Каждая сделка, сигнал журнала и мнение ИИ получают метку «коммит релиза — хеш торговых
настроек». 11.09.2026 размер позиции и блокеры менялись 6–7 раз за день, и без метки
отчёт смешивал результаты разных «ботов».

Коммит подставляет git archive: у release_commit.txt атрибут export-subst в
.gitattributes, а релиз собирается из архива (deploy/ship.sh). В рабочей копии там
шаблон, и версия кода — «dev». Хеш настроек ловит смену профиля режима в .env без
смены кода (например, потолок капитала демо).
"""
import hashlib
import json
from functools import lru_cache
from pathlib import Path

RELEASE_FILE = Path(__file__).resolve().parent.parent / "release_commit.txt"

# Настройки, которые меняют торговое поведение без смены кода (режим сделки пишется
# отдельно — колонка trades.mode)
TRADING_SETTINGS = (
    "REAL_CAPITAL_CAP_USDT", "RISK_PERCENT",
    "RISK_MAX_SINGLE_POSITION_PCT", "RISK_MAX_AGGREGATE_EXPOSURE_PCT", "RISK_MAX_CORRELATED_GROUP_PCT",
    "RISK_MAX_OPEN_POSITIONS", "RISK_MAX_OPEN_RISK_PCT", "RISK_MAX_GROUP_RISK_PCT",
    "RISK_MAX_ACTIONS_PER_HOUR", "RISK_MAX_ACTIONS_PER_24H", "RISK_ACTION_COOLDOWN_SECONDS",
    "MAX_NEW_POSITIONS_PER_TURN", "RISK_LOSS_EVENT_WINDOW_MINUTES",
)


def release_commit() -> str:
    """Первые 12 знаков коммита релиза; «dev» — рабочая копия или файла нет."""
    try:
        text = RELEASE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return "dev"
    return text[:12] if text and not text.startswith("$Format") else "dev"


def settings_hash() -> str:
    import config
    values = {name: getattr(config, name, None) for name in TRADING_SETTINGS}
    return hashlib.sha1(json.dumps(values, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:8]


@lru_cache(maxsize=1)
def version() -> str:
    """Метка «коммит-хеш настроек»; вычисляется один раз на процесс."""
    return f"{release_commit()}-{settings_hash()}"
