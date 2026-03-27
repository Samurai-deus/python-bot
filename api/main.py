"""
FastAPI application — Phase 5 backend for Telegram Mini App.

Run from project root:
    uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""
import os
from contextlib import asynccontextmanager

import sentry_sdk
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

load_dotenv()

_sentry_dsn = os.environ.get("SENTRY_DSN")
if _sentry_dsn:
    sentry_sdk.init(
        dsn=_sentry_dsn,
        traces_sample_rate=0.1,
        environment=os.environ.get("ENVIRONMENT", "production"),
    )

from api.routers import analytics, positions, settings, signals, system, ws


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing to initialise (DB is created lazily on first query)
    yield
    # Shutdown: nothing to clean up for now


app = FastAPI(
    title="Market Bot API",
    description="Backend API for Telegram Mini App",
    version="0.1.0",
    lifespan=lifespan,
)

Instrumentator().instrument(app).expose(app)

# CORS — allow Mini App origin and local dev
_allowed_origins = [
    o.strip()
    for o in os.environ.get(
        "CORS_ORIGINS",
        "https://telegram.org,https://web.telegram.org,http://localhost:5173",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["Content-Type", "X-Telegram-Init-Data"],
)

app.include_router(system.router)
app.include_router(positions.router)
app.include_router(signals.router)
app.include_router(analytics.router)
app.include_router(settings.router)
app.include_router(ws.router)


@app.get("/", include_in_schema=False)
async def root():
    return {"status": "ok", "docs": "/docs"}
