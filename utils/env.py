"""
Единый разбор переменных окружения.

Зачем отдельный модуль на пятнадцать строк кода: до 10.09.2026 булевы флаги
разбирались в трёх местах и тремя разными способами.

    trading_mode.py:28      value.lower() in ("true", "1", "yes", "on")
    exchange/bybit_client.py:109   value.lower() == "true"
    execution/order_executor.py:27 value.lower() == "true"

Расхождение не косметическое. При `BYBIT_TESTNET=1` в .env получалось так:
get_trading_mode() возвращал TESTNET, гейткипер пропускал исполнение как тестовое,
в Telegram уходило сообщение с пометкой [TESTNET] — а BybitClient сравнивал строку
"1" со строкой "true", не находил равенства и выставлял базовый URL на
https://api.bybit.com. То есть реальный ордер на mainnet под видом теста.

Зеркальный случай: `DRY_RUN=1` (именно так делает собственный runtime_tests.sh)
давал «сухой» режим в trading_mode и боевой исполнитель в OrderExecutor.

Правило: любой булев флаг окружения читается ТОЛЬКО через env_flag(). За соблюдением
следит гейт в CI (см. .github/workflows/ci.yml, шаг «единый парсер флагов»).
"""
import os
from typing import Optional

# Истина и ложь перечислены явно и симметрично. Всё остальное — ошибка конфигурации,
# а не «наверное, ложь»: молчаливое приведение мусора к False однажды уже означало бы
# «торгуем на реальные деньги, потому что LIVE_TRADING=yse не распозналось».
_TRUE = frozenset({"true", "1", "yes", "on", "y", "t"})
_FALSE = frozenset({"false", "0", "no", "off", "n", "f", ""})


def env_flag(name: str, default: bool = False) -> bool:
    """
    Читает булев флаг окружения.

    Не задан → default. Задан распознаваемым значением → соответствующий bool.
    Задан чем-то другим → ValueError с указанием имени и значения.
    """
    raw: Optional[str] = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        # Пустая строка — это «переменная объявлена, значение не задано»
        # (`DRY_RUN=` в .env). Трактуем как default, а не как False: иначе
        # пустой LIVE_TRADING= молча означал бы не то, что пустой DRY_RUN=.
        return default if value == "" else False
    raise ValueError(
        f"Переменная окружения {name}={raw!r} не является булевой. "
        f"Допустимо: {sorted(_TRUE)} или {sorted(_FALSE - {''})}"
    )


def env_int(name: str, default: int) -> int:
    """Целое из окружения. Пустая строка = не задано (частый вид в .env-шаблонах)."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ValueError(f"Переменная окружения {name}={raw!r} не является целым числом") from exc


def env_float(name: str, default: float) -> float:
    """Дробное из окружения. Пустая строка = не задано. Мусор — ValueError, не default."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip().replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"Переменная окружения {name}={raw!r} не является числом") from exc


def env_str(name: str, default: str = "") -> str:
    """Строка из окружения с обрезкой пробелов. Пустая строка = не задано."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    return value if value else default
