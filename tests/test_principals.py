"""
Права доступа: закрыто, пока не открыто явно (аудит 10.09.2026, B-5 и C-6).

Раньше все три места, решавшие «кто админ», открывали доступ при пустой
настройке, а шаблон .env.example поставлялся с пустым ADMIN_CHAT_ID.
"""
import pytest

from utils import principals

OWNER = 1001
FRIEND = 2002
STRANGER = 3003


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("ADMIN_CHAT_ID", raising=False)
    monkeypatch.delenv("ALLOWED_USER_IDS", raising=False)


def test_empty_config_denies_everyone():
    """Главная регрессия: пустая настройка больше не значит «всем можно»."""
    assert principals.is_admin(OWNER) is False
    assert principals.is_admin(STRANGER) is False
    assert principals.is_allowed(STRANGER) is False


def test_empty_string_admin_does_not_crash_and_denies(monkeypatch):
    """
    `ADMIN_CHAT_ID=` из шаблона: python-dotenv кладёт пустую строку. Раньше
    telegram_commands делал int("") и падал ValueError при импорте — команды
    бота молча не стартовали, и оператор скорее всего удалял строку, получая
    доступ для всех.
    """
    monkeypatch.setenv("ADMIN_CHAT_ID", "")
    assert principals.admin_id() is None
    assert principals.is_admin(OWNER) is False


def test_non_numeric_admin_denies(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "@my_username")
    assert principals.is_admin(OWNER) is False
    assert principals.is_allowed(OWNER) is False


def test_owner_is_admin_and_allowed(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    assert principals.is_admin(OWNER) is True
    assert principals.is_allowed(OWNER) is True


def test_owner_id_given_as_string_matches(monkeypatch):
    """user_id приходит то числом (PTB), то строкой (JSON из initData)."""
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    assert principals.is_admin(str(OWNER)) is True


def test_stranger_is_neither(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    assert principals.is_admin(STRANGER) is False
    assert principals.is_allowed(STRANGER) is False


def test_observer_can_see_but_not_manage(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    monkeypatch.setenv("ALLOWED_USER_IDS", f"{FRIEND}")
    assert principals.is_allowed(FRIEND) is True
    assert principals.is_admin(FRIEND) is False


def test_allowed_list_tolerates_spaces_and_junk(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    monkeypatch.setenv("ALLOWED_USER_IDS", f" {FRIEND} , , junk ")
    assert principals.allowed_ids() == frozenset({OWNER, FRIEND})


def test_none_and_garbage_user_ids_are_denied(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    for bad in (None, "", "abc", object()):
        assert principals.is_allowed(bad) is False
        assert principals.is_admin(bad) is False


def test_require_configured_raises_when_missing():
    with pytest.raises(RuntimeError, match="ADMIN_CHAT_ID"):
        principals.require_configured()


def test_require_configured_raises_when_not_numeric(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", "admin")
    with pytest.raises(RuntimeError, match="не является числом"):
        principals.require_configured()


def test_require_configured_passes_when_set(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    principals.require_configured()
