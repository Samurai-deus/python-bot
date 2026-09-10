"""
IP клиента за прокси и защищаемые пути (аудит 10.09.2026, C-7 и H-12).

Проверяется чистыми функциями, без поднятого сервера: объект запроса
подменяется простым пространством имён с .client.host и .headers.
"""
from types import SimpleNamespace

import pytest

from api.ip_whitelist import is_ip_allowed, is_protected, parse_whitelist
from utils.client_ip import client_ip

ATTACKER = "203.0.113.7"
NGINX = "127.0.0.1"
DOCKER_GW = "172.17.0.1"


def req(direct, **headers):
    return SimpleNamespace(
        client=SimpleNamespace(host=direct) if direct else None,
        headers={k.replace("_", "-").lower(): v for k, v in headers.items()},
    )


# ---------------------------------------------------------------------------
# Подделка X-Forwarded-For — собственно регрессия
# ---------------------------------------------------------------------------

def test_spoofed_forwarded_for_through_nginx_yields_real_ip():
    """
    `curl -H "X-Forwarded-For: 127.0.0.1"` приезжает от nginx как
    "127.0.0.1, <настоящий IP>": $proxy_add_x_forwarded_for дописывает, а не
    заменяет. Прежняя логика брала первый элемент — подделку — и пропускала
    атакующего в белый список с 127.0.0.1.
    """
    r = req(NGINX, x_forwarded_for=f"127.0.0.1, {ATTACKER}")
    assert client_ip(r) == ATTACKER


def test_real_ip_header_from_proxy_wins():
    """X-Real-IP nginx ставит через proxy_set_header — клиентское значение затирается."""
    r = req(NGINX, x_real_ip=ATTACKER, x_forwarded_for="10.0.0.1, 127.0.0.1")
    assert client_ip(r) == ATTACKER


def test_docker_gateway_is_trusted_proxy():
    """API в контейнере видит nginx хоста как шлюз docker 172.x."""
    r = req(DOCKER_GW, x_real_ip=ATTACKER)
    assert client_ip(r) == ATTACKER


def test_headers_from_untrusted_connection_are_ignored():
    """Если соединение пришло не от нашего прокси, заголовкам не верим вовсе."""
    r = req(ATTACKER, x_real_ip="127.0.0.1", x_forwarded_for="127.0.0.1")
    assert client_ip(r) == ATTACKER


def test_garbage_real_ip_falls_back_to_last_forwarded():
    r = req(NGINX, x_real_ip="not-an-ip", x_forwarded_for=f"1.2.3.4, {ATTACKER}")
    assert client_ip(r) == ATTACKER


def test_proxy_without_headers_returns_proxy_address():
    assert client_ip(req(NGINX)) == NGINX


def test_missing_client_is_unknown():
    assert client_ip(req(None)) == "unknown"


def test_whitelist_bypass_scenario_end_to_end():
    """
    Сценарий из аудита целиком: белый список из .env.example (127.0.0.1),
    атакующий подделывает X-Forwarded-For. Должен получить отказ.
    """
    networks = parse_whitelist("127.0.0.1,10.0.0.1")
    r = req(NGINX, x_forwarded_for=f"127.0.0.1, {ATTACKER}")
    assert is_ip_allowed(client_ip(r), networks) is False


# ---------------------------------------------------------------------------
# Защищаемые пути
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,protected", [
    # H-12: GET/PUT /api/settings без хвостового слэша проходили мимо
    # префикса "/api/settings/"
    ("/api/settings", True),
    ("/api/settings/", True),
    ("/api/settings/keys", True),
    ("/api/system/balance", True),
    ("/metrics", True),
    # Похожие, но чужие пути под защиту попадать не должны
    ("/api/settingsX", False),
    ("/metricsfoo", False),
    # Здоровье системы нужно экрану Mini App у всех допущенных пользователей
    ("/api/system/health", False),
    ("/api/positions", False),
])
def test_is_protected(path, protected):
    assert is_protected(path) is protected


def test_parse_whitelist_skips_invalid_entries():
    nets = parse_whitelist("127.0.0.1, bogus, 10.0.0.0/8,")
    assert len(nets) == 2
