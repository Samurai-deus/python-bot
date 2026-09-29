"""
Каждый сервис из прод-compose настраивает логирование с фильтром токена (28.09.2026).

Портфель, И18, И13 и новости настраивали логирование своим basicConfig — без фильтра и без
приглушения httpx, и каждое уведомление писало в лог контейнера URL с токеном бота. Тест идёт
по списку сервисов из compose, а не по списку имён: новый сервис попадёт под проверку сам.
"""
import io
import logging
import re
from pathlib import Path

import pytest

from utils.log_redaction import SecretRedactingFilter, setup_service_logging

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "deploy" / "docker-compose.prod.yml"
FAKE_TOKEN = "123456789:XYZtestonlynotarealtoken_0000000000"


def compose_modules():
    """Пакеты, которые compose запускает как python -m <пакет>."""
    return re.findall(r'command:\s*\["python",\s*"-m",\s*"(\w+)"\]', COMPOSE.read_text(encoding="utf-8"))


def test_compose_lists_services():
    mods = compose_modules()
    assert {"portfolio", "btcalts", "carry", "news"} <= set(mods), mods


@pytest.mark.parametrize("module", compose_modules())
def test_service_logging_goes_through_redaction(module):
    source = (ROOT / module / "__main__.py").read_text(encoding="utf-8")
    assert "logging.basicConfig(" not in source, f"{module}: basicConfig мимо фильтра токена"
    if re.search(r"^import logging$", source, re.M):
        assert "setup_service_logging()" in source, f"{module}: логирование без setup_service_logging"


@pytest.fixture
def clean_root():
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    levels = {n: logging.getLogger(n).level for n in ("httpx", "httpcore")}
    root.handlers = []
    yield root
    root.handlers, root.level = saved[0], saved[1]
    for name, level in levels.items():
        logging.getLogger(name).setLevel(level)


def test_setup_service_logging_hides_token(clean_root):
    root = setup_service_logging()
    assert root.handlers and all(
        any(isinstance(f, SecretRedactingFilter) for f in h.filters) for h in root.handlers)
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    stream = io.StringIO()
    root.handlers[0].setStream(stream)
    logging.getLogger("httpx").warning("POST https://api.telegram.org/bot%s/sendMessage", FAKE_TOKEN)
    out = stream.getvalue()
    assert "bot123456789:***" in out and FAKE_TOKEN.split(":")[1] not in out
