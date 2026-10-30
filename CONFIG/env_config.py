"""Load typed settings from .env without storing secrets in Python."""

import json
import os
from pathlib import Path

from dotenv import dotenv_values

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
ID_LISTS = {"ADMIN", "ADMIN_GROUP", "ALLOWED_GROUP", "ALLOWED_USERS"}
LOG_CHANNELS = (
    "LOGS_VIDEO_ID", "LOGS_NSFW_ID", "LOGS_IMG_ID", "LOGS_PAID_ID", "LOG_EXCEPTION",
)


def parse_value(key, raw, default):
    """Errors name the key, never the supplied credential value."""
    try:
        if key in ID_LISTS:
            values = json.loads(raw) if raw.strip().startswith("[") else raw.split(",")
            if not isinstance(values, list):
                raise ValueError
            return [int(value) for value in values if str(value).strip()]
        if isinstance(default, bool):
            value = raw.strip().lower()
            if value not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
                raise ValueError
            return value in {"true", "1", "yes", "on"}
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
        if isinstance(default, dict):
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError
            return value
        return raw
    except (ValueError, TypeError):
        raise ValueError(f"Invalid configuration value for {key}; check .env") from None


def configure(config, *, fresh):
    # Docker env_file and explicit process variables override the file.
    values = {**dotenv_values(ENV_PATH, interpolate=False), **os.environ}
    if fresh:
        for key in ID_LISTS:
            setattr(config, key, [])
        config.API_ID = 0
        config.API_HASH = ""
        config.BOT_TOKEN = ""
        config.LOGS_ID = 0
        config.SUBSCRIBE_CHANNEL = 0
        config.SUBSCRIBE_CHANNEL_URL = ""
        config.ADMIN_USERNAME = ""
        config.CHANNEL_GUARD_SESSION_STRING = ""
        for prefix in ("PROXY", "PROXY_2"):
            for field in ("IP", "USER", "PASSWORD"):
                setattr(config, f"{prefix}_{field}", "")

    for key in dir(config):
        if key.isupper() and key in values and values[key] is not None:
            setattr(config, key, parse_value(key, values[key], getattr(config, key)))

    if fresh or "BOT_NAME" in values:
        if "BOT_NAME_FOR_USERS" not in values:
            config.BOT_NAME_FOR_USERS = config.BOT_NAME
        if "MINIAPP_URL" not in values:
            config.MINIAPP_URL = f"https://t.me/{config.BOT_NAME}/?startapp"
    if fresh or "LOGS_ID" in values:
        for key in LOG_CHANNELS:
            if key not in values:
                setattr(config, key, config.LOGS_ID)
    if fresh:
        required = ("BOT_NAME", "API_ID", "API_HASH", "BOT_TOKEN", "ADMIN", "LOGS_ID")
        missing = [key for key in required if not getattr(config, key) or (
            key == "BOT_NAME" and key not in values
        )]
        if missing:
            raise ValueError("Missing required .env settings: " + ", ".join(missing))
    if "BOT_DB_PATH" not in values and (
        fresh or "BOT_NAME_FOR_USERS" in values or "BOT_NAME" in values
    ):
        config.BOT_DB_PATH = f"bot/{config.BOT_NAME_FOR_USERS}/"
