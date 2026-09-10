"""
FastAPI application — backend для Telegram Mini App.

Запуск из корня проекта:
    uvicorn api.main:app --host 0.0.0.0 --port 8000

Приложение собирается фабрикой create_app(): это даёт тестам отдельный экземпляр
на каждый набор переменных окружения. Имя api.main:app сохранено.
"""
import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from api.ip_whitelist import IPWhitelistMiddleware
from api.rate_limit import RateLimitMiddleware
from utils import principals
from utils.env import env_flag, env_str

load_dotenv()

logger = logging.getLogger(__name__)


def is_production() -> bool:
    return env_str("ENVIRONMENT").lower() == "production"


def enforce_security_config() -> None:
    """
    Отказ запускаться в бою с опасной конфигурацией.

    Раньше каждый из этих случаев давал только warning в лог:
      • DISABLE_AUTH / DISABLE_WS_AUTH в бою — все роуты открыты, а заглушка
        пользователя проходила verify_admin, то есть админ — любой;
      • пустой ADMIN_CHAT_ID — админом считался любой вошедший;
      • нет TELEGRAM_BOT_TOKEN — подпись initData проверить нечем.
    Процесс, который не стартовал с понятной ошибкой, заметят сразу; процесс,
    который стартовал с открытыми дверями, — нет.
    """
    if not is_production():
        if env_flag("DISABLE_AUTH") or env_flag("DISABLE_WS_AUTH"):
            logger.warning("DISABLE_AUTH включён вне production (ENVIRONMENT=%r)", env_str("ENVIRONMENT"))
        return

    problems = []
    if env_flag("DISABLE_AUTH") or env_flag("DISABLE_WS_AUTH"):
        problems.append("DISABLE_AUTH/DISABLE_WS_AUTH=true недопустимы при ENVIRONMENT=production")
    try:
        principals.require_configured()
    except RuntimeError as exc:
        problems.append(str(exc))
    if not env_str("TELEGRAM_BOT_TOKEN"):
        problems.append("TELEGRAM_BOT_TOKEN не задан — подпись initData проверить нечем")

    if problems:
        raise RuntimeError("Небезопасная конфигурация API:\n  - " + "\n  - ".join(problems))


def _init_sentry() -> None:
    dsn = env_str("SENTRY_DSN")
    if not dsn:
        return

    def _scrub(event, _hint):
        # initData в заголовке — ключ к данным бота на сутки. Кастомный
        # заголовок не входит в стандартный список маскируемых у Sentry,
        # поэтому при исключении в роуте он уезжал бы в событие целиком.
        headers = (event.get("request") or {}).get("headers") or {}
        for name in list(headers):
            if name.lower() in ("x-telegram-init-data", "authorization", "cookie"):
                headers[name] = "[скрыто]"
        return event

    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=0.1,
        environment=env_str("ENVIRONMENT", "production"),
        send_default_pii=False,
        before_send=_scrub,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    from database import close_pg_pool
    close_pg_pool()
    from api.routers.ws import _ws_executor
    _ws_executor.shutdown(wait=False)


def create_app() -> FastAPI:
    # API тоже ходит в Telegram (уведомление о смене ключей) — токен не должен
    # оказаться в логе и здесь. См. utils/log_redaction.py.
    from utils.log_redaction import install_log_redaction
    install_log_redaction(logging.getLogger())

    enforce_security_config()
    _init_sentry()

    from api.routers import analytics, positions, settings, signals, system, ws

    production = is_production()
    app = FastAPI(
        title="Market Bot API",
        description="Backend API for Telegram Mini App",
        version="0.2.0",
        lifespan=lifespan,
        # Схема API в бою не нужна никому, кроме того, кто ищет, куда постучать
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
        openapi_url=None if production else "/openapi.json",
    )

    Instrumentator().instrument(app).expose(app)

    # localhost:5173 — dev-сервер vite; в бою ему в списке происхождений не место
    default_origins = "https://telegram.org,https://web.telegram.org"
    if not production:
        default_origins += ",http://localhost:5173"
    allowed_origins = [o.strip() for o in os.environ.get("CORS_ORIGINS", default_origins).split(",") if o.strip()]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-Telegram-Init-Data"],
    )
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(IPWhitelistMiddleware)

    app.include_router(system.router)
    app.include_router(positions.router)
    app.include_router(signals.router)
    app.include_router(analytics.router)
    app.include_router(settings.router)
    app.include_router(ws.router)

    @app.get("/", include_in_schema=False)
    async def root():
        return {"status": "ok"}

    return app


app = create_app()
