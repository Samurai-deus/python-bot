"""
Rate limiting middleware using Redis (fixed-window, per-IP).

Limits (configurable via env):
  RATE_LIMIT_PER_MINUTE       — read  (GET/HEAD/OPTIONS)  default 120 req/min per IP
  RATE_LIMIT_WRITE_PER_MINUTE — write (POST/PUT/PATCH/DELETE) default 10 req/min per IP+path

Fail-open: if Redis is unreachable, all requests pass through and an error is logged.
"""
import logging
import os
import time

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Paths that are never rate-limited (prometheus scraper, docker health-check, docs)
_EXCLUDED = frozenset({"/", "/metrics", "/docs", "/openapi.json", "/redoc"})

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Fixed-window rate limiter backed by Redis.

    Each IP gets its own counter per 60-second window.
    Write endpoints additionally get a per-IP-per-path counter with a
    tighter limit to protect state-changing operations.
    """

    def __init__(self, app, redis_url: str | None = None, client=None) -> None:
        super().__init__(app)
        self._read_limit = int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120"))
        self._write_limit = int(os.environ.get("RATE_LIMIT_WRITE_PER_MINUTE", "10"))
        self._client = None

        # Готовый клиент можно передать явно — ради тестов поведения при отказе
        # Redis, без поднятого Redis и без двухсекундного таймаута соединения.
        if client is not None:
            self._client = client
            return

        url = redis_url or os.environ.get("REDIS_URL", "redis://redis:6379")
        try:
            import redis.asyncio as aioredis  # noqa: PLC0415

            self._client = aioredis.from_url(
                url, decode_responses=True, socket_connect_timeout=2
            )
            logger.info(
                "Rate limiting enabled: read=%d/min write=%d/min (Redis: %s)",
                self._read_limit,
                self._write_limit,
                url,
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Redis unavailable — rate limiting disabled: %s", exc)

    @staticmethod
    def _client_ip(request: Request) -> str:
        """
        IP клиента — через общую utils.client_ip. Прежняя копия здесь брала
        первый элемент X-Forwarded-For, то есть присланный клиентом: ротация
        значения давала бесконечно много «разных IP» и обходила лимит.
        """
        from utils.client_ip import client_ip
        return client_ip(request)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip if Redis unavailable, excluded path, or preflight
        if (
            self._client is None
            or request.url.path in _EXCLUDED
            or request.method == "OPTIONS"
        ):
            return await call_next(request)

        is_write = request.method in _WRITE_METHODS
        limit = self._write_limit if is_write else self._read_limit
        window = 60  # seconds

        ip = self._client_ip(request)
        bucket = int(time.time()) // window

        # Write ops: separate counter per path so /api/settings has its own bucket
        key = (
            f"rl:w:{ip}:{request.url.path}:{bucket}"
            if is_write
            else f"rl:r:{ip}:{bucket}"
        )

        # Sliding-window approximation using two adjacent fixed windows.
        # Prevents the 2× burst that a pure fixed-window allows at the boundary:
        #   approx_count = prev_window_count * (1 - elapsed/window) + curr_count
        prev_key = (
            f"rl:w:{ip}:{request.url.path}:{bucket - 1}"
            if is_write
            else f"rl:r:{ip}:{bucket - 1}"
        )
        try:
            async with self._client.pipeline(transaction=False) as pipe:
                pipe.get(prev_key)
                pipe.incr(key)
                results = await pipe.execute()
            prev_count = int(results[0] or 0)
            count = int(results[1])
            if count == 1:
                await self._client.expire(key, window + 5)
        except Exception as exc:
            # В бою при отказе Redis — отказ запросу, а не молчаливое выключение
            # лимита. Раньше лимитер тихо выключался, а заметить это можно было
            # только чтением логов: дефолт REDIS_URL без пароля при compose,
            # требующем --requirepass, давал ошибку AUTH на КАЖДОМ запросе.
            from utils.env import env_str
            if env_str("ENVIRONMENT").lower() == "production":
                logger.error("Rate limit: Redis недоступен, запрос отклонён (fail-closed): %s", exc)
                return Response(
                    content='{"detail":"Service temporarily unavailable"}',
                    status_code=503,
                    media_type="application/json",
                )
            logger.error("Rate limit: Redis недоступен, лимит пропущен (не production): %s", exc)
            return await call_next(request)

        elapsed_in_window = time.time() % window
        approx_count = int(prev_count * (1.0 - elapsed_in_window / window) + count)

        remaining = max(0, limit - approx_count)
        retry_after = window - (int(time.time()) % window)

        if approx_count > limit:
            logger.warning(
                "Rate limit exceeded: ip=%s method=%s path=%s count=%d limit=%d",
                ip,
                request.method,
                request.url.path,
                count,
                limit,
            )
            return Response(
                content='{"detail":"Too Many Requests"}',
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(time.time()) + retry_after),
                },
            )

        response = await call_next(request)
        # Annotate successful responses with current quota state
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + retry_after)
        return response
