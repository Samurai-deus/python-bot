"""
Белый список IP для административных путей.

API_IP_WHITELIST — IP или CIDR через запятую. Пустое значение — проверка
выключена. Это второй рубеж, а не первый: доступ к данным закрывает проверка
пользователя в api/deps.py, которая работает всегда. Белый список добавляет
требование «и приходить с известного адреса» для самых чувствительных путей.

Два исправления от 10.09.2026:

1. Защищаемые пути сравнивались префиксом с хвостовым слэшем: "/api/settings/".
   Роутер же регистрирует GET и PUT на "/api/settings" без слэша — эти запросы
   под защиту не попадали. Теперь путь защищён, если совпадает с префиксом
   целиком или продолжается после него через "/".

2. IP клиента брался из первого элемента X-Forwarded-For — того, что присылает
   сам клиент. Теперь используется utils.client_ip (подробности там).

Убран из защищаемых /api/system/health: им пользуется экран Mini App у всех
допущенных пользователей, и с включённым списком они видели бы Forbidden.
Баланс (/api/system/balance) под защитой остался.
"""
import ipaddress
import logging
import os

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from utils.client_ip import client_ip

logger = logging.getLogger(__name__)

PROTECTED_PATHS = (
    "/api/settings",
    "/api/system/balance",
    "/metrics",
)


def is_protected(path: str) -> bool:
    """Путь под защитой, если равен префиксу или продолжается после него через '/'."""
    return any(path == p or path.startswith(p + "/") for p in PROTECTED_PATHS)


def parse_whitelist(raw: str) -> list:
    """Разбирает список IP/CIDR через запятую; некорректные записи пропускает с ошибкой в лог."""
    networks = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.error("API_IP_WHITELIST: некорректная запись пропущена: %r", entry)
    return networks


def is_ip_allowed(ip_str: str, networks: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        logger.warning("IP whitelist: не удалось разобрать адрес %r — отказ", ip_str)
        return False
    return any(addr in net for net in networks)


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        raw = os.environ.get("API_IP_WHITELIST", "").strip()
        self._networks = parse_whitelist(raw) if raw else []
        if self._networks:
            logger.info("IP whitelist: %d сетей для путей %s", len(self._networks), ", ".join(PROTECTED_PATHS))
        else:
            logger.info("IP whitelist выключен (API_IP_WHITELIST пуст)")

    async def dispatch(self, request: Request, call_next) -> Response:
        if not self._networks or not is_protected(request.url.path):
            return await call_next(request)

        ip = client_ip(request)
        if is_ip_allowed(ip, self._networks):
            return await call_next(request)

        logger.warning("IP whitelist: отказ %s %s с ip=%s", request.method, request.url.path, ip)
        return Response(content='{"detail":"Forbidden"}', status_code=403, media_type="application/json")
