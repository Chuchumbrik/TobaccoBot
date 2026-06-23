"""Доступность сравнения между сайтами."""

from __future__ import annotations

from .hub import ShopHub
from .protocol import ShopProvider
from .types import SiteCapability


def providers_with_search(hub: ShopHub) -> list[ShopProvider]:
    return [p for p in hub._providers if p.has_capability(SiteCapability.SEARCH)]


def is_compare_available(hub: ShopHub) -> bool:
    """True, если есть минимум два сайта с поиском для сравнения."""
    return len(providers_with_search(hub)) >= 2


def comparable_site_labels(hub: ShopHub) -> list[tuple[str, str]]:
    return [(p.info.site_id, p.info.display_name) for p in providers_with_search(hub)]
