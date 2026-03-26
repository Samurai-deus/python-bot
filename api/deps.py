"""
Shared dependencies for FastAPI routes.
"""
import asyncio
import functools
import logging
import os
import hashlib
import hmac
import time
from typing import Optional
from urllib.parse import unquote

from fastapi import Request, HTTPException

logger = logging.getLogger(__name__)


async def run_sync(fn, *args, **kwargs):
    """Run a synchronous function in the default thread pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


def verify_ws_token(init_data: str) -> bool:
    """
    Validate Telegram InitData for WebSocket connections (query-param based).
    Returns True if valid, DISABLE_AUTH=true, or DISABLE_WS_AUTH=true.
    Uses 24-hour expiry (vs 5 min for HTTP) since WS connections are long-lived
    and the token is retrieved once at app startup.
    """
    if os.environ.get("DISABLE_AUTH", "false").lower() == "true":
        logger.warning("DISABLE_AUTH=true: WS authentication is disabled — do NOT use in production")
        return True
    if os.environ.get("DISABLE_WS_AUTH", "false").lower() == "true":
        logger.warning("DISABLE_WS_AUTH=true: WS authentication is disabled")
        return True
    if not init_data:
        return False
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        return False
    params = {}
    hash_value = None
    for part in init_data.split("&"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = unquote(key)
        value = unquote(value)
        if key == "hash":
            hash_value = value
        else:
            params[key] = value
    if hash_value is None:
        return False
    try:
        auth_date = int(params.get("auth_date", 0))
    except (ValueError, TypeError):
        return False
    if abs(time.time() - auth_date) > 86400:  # 24h for WS (token retrieved once at app start)
        return False
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, hash_value)


async def verify_auth(request: Request) -> Optional[dict]:
    """
    Validate Telegram InitData HMAC-SHA256.
    If DISABLE_AUTH=true, skip validation and return a stub user.
    """
    if os.environ.get("DISABLE_AUTH", "false").lower() == "true":
        logger.warning("DISABLE_AUTH=true: HTTP authentication is disabled — do NOT use in production")
        return {"user_id": 0, "username": "dev"}

    init_data = request.headers.get("X-Telegram-Init-Data")
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram InitData")

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not bot_token:
        raise HTTPException(status_code=500, detail="Bot token not configured")

    # Parse init_data string
    params = {}
    hash_value = None
    for part in init_data.split("&"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = unquote(key)
        value = unquote(value)
        if key == "hash":
            hash_value = value
        else:
            params[key] = value

    if hash_value is None:
        raise HTTPException(status_code=401, detail="Missing hash in InitData")

    # Check timestamp freshness (5 minutes)
    try:
        auth_date = int(params.get("auth_date", 0))
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid auth_date in InitData")
    if abs(time.time() - auth_date) > 300:
        raise HTTPException(status_code=401, detail="InitData expired")

    # Build check string
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))

    # HMAC-SHA256 with secret key = HMAC-SHA256("WebAppData", bot_token)
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, hash_value):
        raise HTTPException(status_code=401, detail="Invalid InitData signature")

    return params
