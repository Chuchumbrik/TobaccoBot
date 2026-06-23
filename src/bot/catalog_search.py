"""Параллельный поиск по каталогу с объединением результатов."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

from oshisha import catalog_cache
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

from bot import service_async as osh

if TYPE_CHECKING:
    from shops.protocol import ShopProvider

logger = logging.getLogger(__name__)


def _site_id(service: ShopProvider) -> str:
    return getattr(getattr(service, "info", None), "site_id", None) or "oshisha"


def _count_catalog_calls(http_calls: int, service: ShopProvider) -> int:
    """При готовом снимке поиск локальный — HTTP не считаем."""
    return 0 if catalog_cache.is_ready(_site_id(service)) else http_calls


def _max_parallel() -> int:
    return max(1, int(os.environ.get("OSHISHA_MAX_PARALLEL", "3")))


async def search_one(
    service: ShopProvider,
    query: str,
    *,
    limit: int,
    sem: asyncio.Semaphore,
) -> FlavorSearchResult | Exception:
    async with sem:
        try:
            return await osh.search_flavor(service, query, limit=limit)
        except Exception as exc:
            return exc


async def merge_flavor_searches(
    service: ShopProvider,
    queries: list[str],
    *,
    limit: int = 50,
    max_parallel: int | None = None,
    max_total_hits: int | None = None,
) -> tuple[list[FlavorSearchHit], int]:
    """
    Параллельный поиск по списку запросов, дедуп по имени товара.
    Возвращает (hits, catalog_call_count).
    """
    if not queries:
        return [], 0

    sem = asyncio.Semaphore(max_parallel or _max_parallel())
    tasks = [search_one(service, q, limit=limit, sem=sem) for q in queries]
    results = await asyncio.gather(*tasks)

    seen: set[str] = set()
    merged: list[FlavorSearchHit] = []
    catalog_calls = 0

    for q, raw in zip(queries, results):
        if isinstance(raw, Exception):
            logger.warning("catalog search failed for %r: %s", q, raw)
            continue
        catalog_calls += 1
        for hit in raw.hits:
            key = hit.product.name.lower().strip()
            if key in seen:
                continue
            seen.add(key)
            merged.append(hit)

    merged.sort(key=lambda h: (h.status != "есть", h.flavor_rank, -h.match_score))
    cap = max_total_hits if max_total_hits is not None else limit
    return merged[:cap], _count_catalog_calls(catalog_calls, service)


async def search_terms_parallel(
    service: ShopProvider,
    terms: list[str],
    *,
    limit_per_term: int = 5,
    max_parallel: int | None = None,
) -> tuple[list[FlavorSearchHit], int]:
    """Поиск по терминам таксономии (миксы), объединённый список."""
    return await merge_flavor_searches(
        service,
        terms,
        limit=limit_per_term,
        max_parallel=max_parallel,
        max_total_hits=None,
    )


async def search_terms_as_map(
    service: ShopProvider,
    terms: list[str],
    *,
    limit_per_term: int = 5,
    max_parallel: int | None = None,
) -> tuple[dict[str, list[FlavorSearchHit]], int]:
    """Параллельный поиск: термин → список хитов."""
    unique = list(dict.fromkeys(t for t in terms if t.strip()))
    if not unique:
        return {}, 0

    sem = asyncio.Semaphore(max_parallel or _max_parallel())
    tasks = [search_one(service, t, limit=limit_per_term, sem=sem) for t in unique]
    results = await asyncio.gather(*tasks)

    out: dict[str, list[FlavorSearchHit]] = {}
    catalog_calls = 0
    for term, raw in zip(unique, results):
        if isinstance(raw, Exception):
            logger.warning("term search failed for %r: %s", term, raw)
            continue
        out[term] = list(raw.hits)
        catalog_calls += 1
    return out, _count_catalog_calls(catalog_calls, service)
