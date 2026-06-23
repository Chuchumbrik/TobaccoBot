"""Мульти-сайтовый слой: реестр магазинов, сравнение, ShopHub."""

from .compare import compare_list, compare_search, summarize_search_prices
from .compare_meta import is_compare_available
from .format_compare import format_compare_list, format_compare_search
from .hub import ShopHub
from .protocol import ShopProvider
from .registry import (
    create_provider,
    create_providers,
    parse_site_ids_from_env,
    register_site,
    registered_site_ids,
)
from .types import (
    CompareListResult,
    CompareSearchResult,
    SiteCapability,
    SiteInfo,
)

__all__ = [
    "ShopHub",
    "ShopProvider",
    "SiteCapability",
    "SiteInfo",
    "register_site",
    "registered_site_ids",
    "create_provider",
    "create_providers",
    "parse_site_ids_from_env",
    "compare_search",
    "compare_list",
    "summarize_search_prices",
    "CompareSearchResult",
    "CompareListResult",
    "format_compare_search",
    "format_compare_list",
    "is_compare_available",
]
