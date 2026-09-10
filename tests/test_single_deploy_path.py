"""
Один путь деплоя (4.1) и README, который не расходится с репозиторием (6.2) — 10.09.2026.

Прежние systemd-юниты, установочные скрипты и три разных deploy.sh описывали
другой сервер и разошлись с кодом: следующий, кто откроет репозиторий, мог
выкатить бота по устаревшей инструкции. Путь один — deploy/. А из 17 ссылок
README на документы 14 вели туда, где файлов не было.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

REMOVED = [
    "market-bot.service", "market-bot.service.new", "miniapp/market-bot-api.service",
    "setup_service.sh", "setup_ubuntu_server.sh", "install.sh", "start_bot.bat",
    "deploy.sh", "miniapp/deploy.sh", "scripts/deploy.sh", "scripts/server-setup.sh",
    "scripts/nginx-signabot.conf", "scripts/backup.sh", "scripts/restore.sh", "scripts/cleanup_disk.sh",
    "operations/SERVER_SETUP.md", "operations/SERVICE_SETUP.md", "operations/START_BOT.md",
]


@pytest.mark.parametrize("path", REMOVED)
def test_old_deploy_path_is_gone(path):
    assert not (ROOT / path).exists(), f"{path} — устаревший путь деплоя вернулся"


def test_the_deploy_path_exists():
    for path in ("deploy/deploy.sh", "deploy/ship.sh", "deploy/README.md", "deploy/docker-compose.prod.yml"):
        assert (ROOT / path).is_file()


def test_readme_links_resolve():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    links = [link for link in re.findall(r"\]\(([^)#\s]+)\)", text) if not re.match(r"[a-z]+://", link)]
    assert links, "в README нет ссылок на документы"
    missing = [link for link in links if not (ROOT / link).exists()]
    assert missing == [], f"битые ссылки в README: {missing}"


def test_readme_teaches_the_current_way():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "systemctl" not in text, "README снова учит запускать бота systemd-юнитом"
    assert "deploy/ship.sh" in text
    assert "PAPER_TRADING" in text and "LIVE_TRADING" in text, "таблица режимов торговли"
