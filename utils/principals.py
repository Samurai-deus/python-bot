"""
Кто владелец бота и кому разрешено его видеть — единственный источник ответа.

До 10.09.2026 это решалось в трёх местах, и все три были открыты по умолчанию:

    api/deps.py:135            пустой ADMIN_CHAT_ID → любой вошедший считается админом
    telegram_commands.py:32    ADMIN_CHAT_ID == 0   → _is_admin() возвращает True всем
    claude_assistant.py:80     пустой ALLOWED_USER_IDS → доступ всем

А шаблон .env.example поставлялся с пустым `ADMIN_CHAT_ID=`. Сценарий из аудита
(блокер B-5): любой владелец аккаунта Telegram находит бота, открывает Mini App —
Telegram сам выдаёт ему валидно подписанный initData, — и делает PUT
/api/settings/keys, подменяя зашифрованные ключи Bybit своими. После рестарта бот
торгует на чужом аккаунте.

Второй слой той же дыры (C-6): даже с заполненным ADMIN_CHAT_ID на ЧТЕНИЕ не
проверялось ничего, кроме подписи. Баланс, позиции со стопами, история сделок и
живой поток по WebSocket были доступны любому пользователю Telegram.

Правила здесь:
  • всё закрыто, пока не открыто явно: нет владельца — нет доступа ни у кого;
  • владелец всегда входит в число допущенных;
  • ALLOWED_USER_IDS добавляет наблюдателей — они видят, но не управляют;
  • значения читаются из окружения при каждом вызове: дёшево, и тесты не
    зависят от порядка импорта.
"""
import logging
from typing import FrozenSet, Optional

from utils.env import env_str

logger = logging.getLogger(__name__)


def admin_id() -> Optional[int]:
    """Telegram user id владельца или None, если не задан или задан не числом."""
    raw = env_str("ADMIN_CHAT_ID")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        # Не падаем здесь — это делает require_configured() на старте. Здесь
        # только отказываем: нечисловой id не совпадёт ни с кем, и это правильно.
        logger.error("ADMIN_CHAT_ID=%r не является числом — доступ владельца закрыт", raw)
        return None


def allowed_ids() -> FrozenSet[int]:
    """Все, кому можно смотреть: владелец плюс ALLOWED_USER_IDS."""
    ids = set()
    for part in env_str("ALLOWED_USER_IDS").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError:
            logger.error("ALLOWED_USER_IDS: пропущено нечисловое значение %r", part)
    owner = admin_id()
    if owner is not None:
        ids.add(owner)
    return frozenset(ids)


def _as_int(user_id) -> Optional[int]:
    if user_id is None:
        return None
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return None


def is_admin(user_id) -> bool:
    """Владелец ли это. Без настроенного ADMIN_CHAT_ID — никто."""
    owner = admin_id()
    uid = _as_int(user_id)
    return owner is not None and uid is not None and uid == owner


def is_allowed(user_id) -> bool:
    """Можно ли этому пользователю видеть данные бота. Без настройки — никому."""
    uid = _as_int(user_id)
    return uid is not None and uid in allowed_ids()


def require_configured() -> None:
    """
    Проверка на старте сервиса: без владельца сервис работать не должен.

    Отсутствие ADMIN_CHAT_ID раньше молча открывало доступ всем, поэтому
    теперь это ошибка запуска с понятным текстом, а не предупреждение в логе,
    которое никто не читает.
    """
    raw = env_str("ADMIN_CHAT_ID")
    if not raw:
        raise RuntimeError(
            "ADMIN_CHAT_ID не задан. Без него доступ к боту закрыт для всех. "
            "Укажите свой Telegram user id (узнать можно у @userinfobot)."
        )
    try:
        int(raw)
    except ValueError:
        raise RuntimeError(f"ADMIN_CHAT_ID={raw!r} не является числом (нужен Telegram user id)")
