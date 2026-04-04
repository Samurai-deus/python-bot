"""
Settings endpoints — GET /api/settings, PUT /api/settings.
Also: GET/PUT /api/settings/keys — encrypted API key management.
"""
import json
import os
from typing import List

from fastapi import APIRouter, Depends, HTTPException
import pydantic
from pydantic import BaseModel

import config
import database
from api.deps import run_sync, verify_auth, verify_admin
from api.models import SettingItem, SettingsResponse, UpdateSettingsRequest

router = APIRouter(prefix="/api/settings", tags=["settings"])

RESTART_REQUIRED_KEYS = {"trading_mode", "bot_interval", "symbols_enabled"}

DEFAULTS: dict = {
    "risk_percent": {
        "value": str(config.RISK_PERCENT),
        "data_type": "float",
    },
    "min_position_size": {
        "value": str(config.MIN_POSITION_SIZE),
        "data_type": "float",
    },
    "max_position_size": {
        "value": str(config.MAX_POSITION_SIZE),
        "data_type": "float",
    },
    "bot_interval": {
        "value": os.environ.get("BOT_INTERVAL", "300"),
        "data_type": "int",
    },
    "trading_mode": {
        "value": "DRY_RUN",
        "data_type": "str",
    },
    "symbols_enabled": {
        "value": json.dumps(config.SYMBOLS),
        "data_type": "json",
    },
}


def _coerce_value(raw: str, data_type: str):
    """Convert stored string value to its typed Python equivalent."""
    if data_type == "float":
        return float(raw)
    if data_type == "int":
        return int(raw)
    if data_type == "bool":
        return raw.lower() in ("true", "1", "yes")
    if data_type == "json":
        return json.loads(raw)
    return raw


def _build_settings_response(db_overrides: dict) -> SettingsResponse:
    """Merge DEFAULTS with DB overrides and return SettingsResponse."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    items: list[SettingItem] = []
    restart_keys: list[str] = []

    for key, default in DEFAULTS.items():
        if key in db_overrides:
            raw = db_overrides[key]["value"]
            data_type = db_overrides[key]["data_type"]
            updated_at = db_overrides[key].get("updated_at", now)
        else:
            raw = default["value"]
            data_type = default["data_type"]
            updated_at = now

        items.append(
            SettingItem(
                key=key,
                value=_coerce_value(raw, data_type),
                data_type=data_type,
                updated_at=updated_at,
            )
        )

    # Collect restart-required keys that were overridden
    for key in RESTART_REQUIRED_KEYS:
        if key in db_overrides:
            restart_keys.append(key)

    return SettingsResponse(settings=items, requires_restart=restart_keys)


@router.get("", response_model=SettingsResponse)
async def get_settings(_user=Depends(verify_auth)):
    db_overrides = await run_sync(database.get_all_settings)
    return _build_settings_response(db_overrides)


@router.put("", response_model=SettingsResponse)
async def update_settings(body: UpdateSettingsRequest, _user=Depends(verify_admin)):
    VALIDATORS = {
        "risk_percent": (0.5, 5.0),
        "min_position_size": (10.0, 500.0),
        "max_position_size": (100.0, 5000.0),
        "bot_interval": (60, 600),
    }

    VALID_TRADING_MODES = {"DRY_RUN", "PAPER_TRADING"}

    updates: list[tuple[str, str, str]] = []

    if body.risk_percent is not None:
        lo, hi = VALIDATORS["risk_percent"]
        if not lo <= body.risk_percent <= hi:
            raise HTTPException(400, f"risk_percent must be between {lo} and {hi}")
        updates.append(("risk_percent", str(body.risk_percent), "float"))

    if body.min_position_size is not None:
        lo, hi = VALIDATORS["min_position_size"]
        if not lo <= body.min_position_size <= hi:
            raise HTTPException(400, f"min_position_size must be between {lo} and {hi}")
        updates.append(("min_position_size", str(body.min_position_size), "float"))

    if body.max_position_size is not None:
        lo, hi = VALIDATORS["max_position_size"]
        if not lo <= body.max_position_size <= hi:
            raise HTTPException(400, f"max_position_size must be between {lo} and {hi}")
        updates.append(("max_position_size", str(body.max_position_size), "float"))

    if body.bot_interval is not None:
        lo, hi = VALIDATORS["bot_interval"]
        if not lo <= body.bot_interval <= hi:
            raise HTTPException(400, f"bot_interval must be between {lo} and {hi}")
        updates.append(("bot_interval", str(body.bot_interval), "int"))

    if body.trading_mode is not None:
        if body.trading_mode not in VALID_TRADING_MODES:
            raise HTTPException(400, f"trading_mode must be one of {VALID_TRADING_MODES}")
        updates.append(("trading_mode", body.trading_mode, "str"))

    if body.symbols_enabled is not None:
        if not body.symbols_enabled:
            raise HTTPException(400, "symbols_enabled must not be empty")
        invalid = [s for s in body.symbols_enabled if s not in config.SYMBOLS]
        if invalid:
            raise HTTPException(400, f"Unknown symbols: {invalid}. Valid: {config.SYMBOLS}")
        updates.append(("symbols_enabled", json.dumps(body.symbols_enabled), "json"))

    for key, value, data_type in updates:
        await run_sync(database.set_setting, key, value, data_type)

    db_overrides = await run_sync(database.get_all_settings)
    return _build_settings_response(db_overrides)


# ============================================================================
# API KEY MANAGEMENT  (encrypted storage)
# ============================================================================

_ALLOWED_KEY_NAMES = frozenset({"BYBIT_API_KEY", "BYBIT_API_SECRET"})


class StoreKeysRequest(BaseModel):
    bybit_api_key: str = pydantic.Field(max_length=256)
    bybit_api_secret: str = pydantic.Field(max_length=256)


class KeyEntry(BaseModel):
    key_name: str
    updated_at: str


class KeysResponse(BaseModel):
    keys: List[KeyEntry]
    encryption: str = "fernet-aes128"


@router.get("/keys", response_model=KeysResponse)
async def list_keys(_user=Depends(verify_auth)):
    """List stored API key names (values are never returned)."""
    entries = await run_sync(database.list_encrypted_key_names)
    return KeysResponse(keys=[KeyEntry(**e) for e in entries])


@router.put("/keys", response_model=KeysResponse)
async def store_keys(body: StoreKeysRequest, _user=Depends(verify_admin)):
    """
    Encrypt and store Bybit API credentials in the database.

    Requires ENCRYPTION_KEY to be configured in .env.
    Values are encrypted with Fernet (AES-128-CBC + HMAC) before storage.
    """
    if not body.bybit_api_key.strip():
        raise HTTPException(400, "bybit_api_key must not be empty")
    if not body.bybit_api_secret.strip():
        raise HTTPException(400, "bybit_api_secret must not be empty")

    try:
        from utils.crypto import encrypt
    except ImportError:
        raise HTTPException(500, "cryptography package not installed")

    try:
        enc_key = encrypt(body.bybit_api_key.strip())
        enc_secret = encrypt(body.bybit_api_secret.strip())
    except RuntimeError as e:
        raise HTTPException(500, f"Encryption error: {e}")

    await run_sync(database.save_encrypted_api_key, "BYBIT_API_KEY", enc_key)
    await run_sync(database.save_encrypted_api_key, "BYBIT_API_SECRET", enc_secret)

    entries = await run_sync(database.list_encrypted_key_names)
    return KeysResponse(keys=[KeyEntry(**e) for e in entries])
