"""
IP whitelist middleware for admin API endpoints.

Reads API_IP_WHITELIST env var (comma-separated IPs or CIDR blocks).
If the env var is empty — all IPs are allowed (fail-open, backward compatible).

Protected path prefixes:
  /api/system/   — system health, balance details
  /api/settings/ — user settings management
  /metrics       — Prometheus metrics (should only be scraped internally)

All other paths are not affected.

Supports X-Forwarded-For (nginx passes real client IP).
"""
import ipaddress
import logging
import os

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Path prefixes that require whitelisted IP
_PROTECTED_PREFIXES = (
    "/api/system/",
    "/api/settings/",
    "/metrics",
)


def _parse_whitelist(raw: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Parse comma-separated IP / CIDR list into network objects."""
    networks = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            # Treat bare IPs as /32 or /128 host networks
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            logger.warning("IP whitelist: invalid entry ignored: %r", entry)
    return networks


def _client_ip(request: Request) -> str:
    """Return the real client IP, honouring nginx X-Forwarded-For."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_allowed(ip_str: str, networks: list) -> bool:
    """Return True if ip_str is in any of the allowed networks."""
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        logger.warning("IP whitelist: cannot parse client IP %r — denying", ip_str)
        return False
    return any(addr in net for net in networks)


class IPWhitelistMiddleware(BaseHTTPMiddleware):
    """
    Blocks requests to protected endpoints from non-whitelisted IPs.

    Empty API_IP_WHITELIST → allow all (fail-open).
    Non-empty API_IP_WHITELIST → enforce for protected prefixes.
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        raw = os.environ.get("API_IP_WHITELIST", "").strip()
        self._networks = _parse_whitelist(raw) if raw else []

        if self._networks:
            logger.info(
                "IP whitelist active (%d network(s)) for prefixes: %s",
                len(self._networks),
                ", ".join(_PROTECTED_PREFIXES),
            )
        else:
            logger.info("IP whitelist disabled (API_IP_WHITELIST is empty — all IPs allowed)")

    def _is_protected(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in _PROTECTED_PREFIXES)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip enforcement if whitelist is not configured
        if not self._networks:
            return await call_next(request)

        # Only enforce on protected paths
        if not self._is_protected(request.url.path):
            return await call_next(request)

        ip = _client_ip(request)
        if _is_allowed(ip, self._networks):
            return await call_next(request)

        logger.warning(
            "IP whitelist: blocked %s %s from ip=%s",
            request.method,
            request.url.path,
            ip,
        )
        return Response(
            content='{"detail":"Forbidden"}',
            status_code=403,
            media_type="application/json",
        )
