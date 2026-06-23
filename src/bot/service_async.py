"""Неблокирующие вызовы синхронного OshishaService (httpx в thread pool)."""

from __future__ import annotations

import asyncio
from typing import TypeVar

from oshisha.cart import CartAddBatchResult, CartView
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult
from shops.hub import ShopHub
from shops.protocol import ShopProvider
from shops.types import CompareListResult, CompareSearchResult

T = TypeVar("T")

# ShopHub и OshishaShopProvider реализуют те же методы, что OshishaService.
ShopService = ShopProvider


async def run_sync(func, /, *args, **kwargs) -> T:
    return await asyncio.to_thread(func, *args, **kwargs)


async def check_list(service: ShopService, lines: list[str]) -> list[ProductCheckResult]:
    return await run_sync(service.check_list, lines)


async def add_to_cart(service: ShopService, lines: list[str]) -> CartAddBatchResult:
    return await run_sync(service.add_to_cart, lines)


async def add_checks_to_cart(
    service: ShopService,
    checks: list[ProductCheckResult],
    *,
    indices: list[int] | None = None,
) -> CartAddBatchResult:
    return await run_sync(service.add_checks_to_cart, checks, indices=indices)


async def add_flavor_hits_to_cart(
    service: ShopService,
    hits: list[FlavorSearchHit],
    indices: list[int],
) -> CartAddBatchResult:
    return await run_sync(service.add_flavor_hits_to_cart, hits, indices)


async def view_cart(service: ShopService) -> CartView:
    return await run_sync(service.view_cart)


async def warmup_catalog(service: ShopService, *, force: bool = False):
    if hasattr(service, "inner"):
        return await run_sync(service.inner.warmup_catalog, force=force)
    if hasattr(service, "warmup_catalog"):
        return await run_sync(service.warmup_catalog, force=force)
    raise TypeError("warmup_catalog not supported for this provider")


async def warmup_all_catalogs(hub: ShopHub, *, force: bool = False):
    return await run_sync(hub.warmup_all_catalogs, force=force)


async def search_flavor(
    service: ShopService,
    query: str,
    *,
    limit: int = 50,
    in_stock_only: bool = False,
) -> FlavorSearchResult:
    return await run_sync(
        service.search_flavor,
        query,
        limit=limit,
        in_stock_only=in_stock_only,
    )


async def compare_search_flavor(
    hub: ShopHub,
    query: str,
    *,
    limit: int = 50,
    in_stock_only: bool = False,
    site_ids: list[str] | None = None,
) -> CompareSearchResult:
    return await run_sync(
        hub.compare_search_flavor,
        query,
        limit=limit,
        in_stock_only=in_stock_only,
        site_ids=site_ids,
    )


async def compare_check_list(
    hub: ShopHub,
    lines: list[str],
    *,
    site_ids: list[str] | None = None,
) -> CompareListResult:
    return await run_sync(hub.compare_check_list, lines, site_ids=site_ids)
