"""Настройки бота из окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]


def _parse_admin_ids(raw: str) -> frozenset[int]:
    ids: set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return frozenset(ids)


@dataclass(frozen=True)
class BotConfig:
    telegram_token: str
    flavor_search_limit: int = 50
    check_list_max_lines: int = 30
    telegram_proxy: str | None = None
    telegram_api_base_url: str | None = None
    telegram_connect_timeout: float = 30.0
    telegram_admin_ids: frozenset[int] = frozenset()
    cart_log_path: Path | None = None
    cart_log_display_limit: int = 25


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

    log_path = os.environ.get("CART_LOG_PATH", "").strip()
    return BotConfig(
        telegram_token=token,
        flavor_search_limit=int(os.environ.get("FLAVOR_SEARCH_LIMIT", "50")),
        check_list_max_lines=int(os.environ.get("CHECK_LIST_MAX_LINES", "30")),
        telegram_proxy=_env_proxy(),
        telegram_api_base_url=base_url,
        telegram_connect_timeout=float(
            os.environ.get("TELEGRAM_CONNECT_TIMEOUT", "30")
        ),
        telegram_admin_ids=_parse_admin_ids(
            os.environ.get("TELEGRAM_ADMIN_IDS", "")
        ),
        cart_log_path=Path(log_path) if log_path else ROOT / "data" / "cart_log.jsonl",
        cart_log_display_limit=int(os.environ.get("CART_LOG_DISPLAY_LIMIT", "25")),
    )
