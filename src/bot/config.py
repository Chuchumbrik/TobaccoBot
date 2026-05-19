"""Настройки бота из окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str
    flavor_search_limit: int = 15
    check_list_max_lines: int = 30
    telegram_proxy: str | None = None
    telegram_api_base_url: str | None = None
    telegram_connect_timeout: float = 30.0


def _env_proxy() -> str | None:
    for key in ("TELEGRAM_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return None


def load_config() -> BotConfig:
    load_dotenv(ROOT / ".env")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN не задан в .env")

    base_url = os.environ.get("TELEGRAM_API_BASE_URL", "").strip() or None

    return BotConfig(
        telegram_token=token,
        flavor_search_limit=int(os.environ.get("FLAVOR_SEARCH_LIMIT", "15")),
        check_list_max_lines=int(os.environ.get("CHECK_LIST_MAX_LINES", "30")),
        telegram_proxy=_env_proxy(),
        telegram_api_base_url=base_url,
        telegram_connect_timeout=float(
            os.environ.get("TELEGRAM_CONNECT_TIMEOUT", "30")
        ),
    )
