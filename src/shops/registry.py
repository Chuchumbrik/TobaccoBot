"""Реестр провайдеров: регистрация новых сайтов только в коде."""

from __future__ import annotations

import os
from collections.abc import Callable

from .protocol import ShopProvider

ProviderFactory = Callable[[], ShopProvider]

_REGISTRY: dict[str, ProviderFactory] = {}
_DEFAULT_SITE = "oshisha"


def register_site(site_id: str, factory: ProviderFactory) -> None:
    """Зарегистрировать фабрику провайдера (вызывать при импорте модуля sites.*)."""
    sid = site_id.strip().lower()
    if not sid:
        raise ValueError("site_id не может быть пустым")
    _REGISTRY[sid] = factory


def unregister_site(site_id: str) -> None:
    _REGISTRY.pop(site_id.strip().lower(), None)


def registered_site_ids() -> list[str]:
    return sorted(_REGISTRY.keys())


def create_provider(site_id: str) -> ShopProvider:
    sid = site_id.strip().lower()
    factory = _REGISTRY.get(sid)
    if factory is None:
        known = ", ".join(registered_site_ids()) or "(пусто)"
        raise KeyError(f"Неизвестный сайт {site_id!r}. Зарегистрированы: {known}")
    return factory()


def parse_site_ids_from_env(
    *,
    env_key: str = "SHOP_SITES",
    default: str | None = None,
) -> list[str]:
    """
    SHOP_SITES=oshisha,demo — порядок важен: первый = primary для бота.
    Пусто → только oshisha (если зарегистрирован).
    """
    raw = os.environ.get(env_key, default or _DEFAULT_SITE).strip()
    if not raw:
        raw = _DEFAULT_SITE
    ids = [p.strip().lower() for p in raw.replace(";", ",").split(",") if p.strip()]
    return ids or [_DEFAULT_SITE]


def create_providers(site_ids: list[str] | None = None) -> list[ShopProvider]:
    ids = site_ids or parse_site_ids_from_env()
    return [create_provider(sid) for sid in ids]
