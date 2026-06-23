"""Сравнение поиска и проверки списков на нескольких сайтах."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from oshisha.catalog import ProductCheckResult

from .protocol import ShopProvider
from .types import (
    CompareLineRow,
    CompareListResult,
    ComparePriceRow,
    CompareQuerySummary,
    CompareSearchResult,
    SiteCapability,
    SiteCheckBatch,
    SiteFlavorSearch,
)

logger = logging.getLogger(__name__)


def _search_one(
    provider: ShopProvider,
    query: str,
    *,
    limit: int,
    in_stock_only: bool,
) -> SiteFlavorSearch:
    info = provider.info
    if not provider.has_capability(SiteCapability.SEARCH):
        return SiteFlavorSearch(
            site_id=info.site_id,
            site_name=info.display_name,
            error="поиск не поддерживается",
        )
    try:
        result = provider.search_flavor(
            query, limit=limit, in_stock_only=in_stock_only
        )
        return SiteFlavorSearch(
            site_id=info.site_id,
            site_name=info.display_name,
            result=result,
        )
    except Exception as exc:
        logger.warning("search failed site=%s query=%r: %s", info.site_id, query, exc)
        return SiteFlavorSearch(
            site_id=info.site_id,
            site_name=info.display_name,
            error=str(exc),
        )


def compare_search(
    providers: list[ShopProvider],
    query: str,
    *,
    limit: int = 15,
    in_stock_only: bool = False,
    max_workers: int | None = None,
) -> CompareSearchResult:
    """Параллельный поиск по вкусу на всех переданных провайдерах."""
    workers = max_workers or min(len(providers), 4) or 1
    outcomes: list[SiteFlavorSearch] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _search_one, p, query, limit=limit, in_stock_only=in_stock_only
            ): p
            for p in providers
        }
        for fut in as_completed(futures):
            outcomes.append(fut.result())

    outcomes.sort(key=lambda o: o.site_id)
    return CompareSearchResult(query=query, limit=limit, sites=outcomes)


def _check_batch_one(provider: ShopProvider, lines: list[str]) -> SiteCheckBatch:
    info = provider.info
    if not provider.has_capability(SiteCapability.CHECK):
        return SiteCheckBatch(
            site_id=info.site_id,
            site_name=info.display_name,
            error="проверка списка не поддерживается",
        )
    try:
        return SiteCheckBatch(
            site_id=info.site_id,
            site_name=info.display_name,
            results=provider.check_list(lines),
        )
    except Exception as exc:
        logger.warning("check_list failed site=%s: %s", info.site_id, exc)
        return SiteCheckBatch(
            site_id=info.site_id,
            site_name=info.display_name,
            error=str(exc),
        )


def compare_list(
    providers: list[ShopProvider],
    lines: list[str],
    *,
    max_workers: int | None = None,
) -> CompareListResult:
    """Проверить те же строки на каждом сайте."""
    workers = max_workers or min(len(providers), 4) or 1
    batches: list[SiteCheckBatch] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_check_batch_one, p, lines): p for p in providers}
        for fut in as_completed(futures):
            batches.append(fut.result())

    batches.sort(key=lambda b: b.site_id)
    site_names = {b.site_id: b.site_name for b in batches}
    site_ids = [b.site_id for b in batches]

    rows: list[CompareLineRow] = []
    for i, line in enumerate(lines):
        row = CompareLineRow(query=line)
        for batch in batches:
            if batch.error:
                row.errors[batch.site_id] = batch.error
                continue
            if i < len(batch.results):
                row.by_site[batch.site_id] = batch.results[i]
        rows.append(row)

    return CompareListResult(
        lines=rows,
        site_ids=site_ids,
        site_names=site_names,
    )


def summarize_search_prices(
    compare: CompareSearchResult,
    *,
    top_per_site: int = 3,
) -> CompareQuerySummary:
    """Сводка: лучшие совпадения и цены по каждому сайту."""
    rows: list[ComparePriceRow] = []
    for site in compare.sites:
        if site.error or site.result is None:
            rows.append(
                ComparePriceRow(
                    site_id=site.site_id,
                    site_name=site.site_name,
                    product_name=None,
                    price=None,
                    currency=None,
                    status="ошибка",
                )
            )
            continue
        hits = site.result.hits[:top_per_site]
        if not hits:
            rows.append(
                ComparePriceRow(
                    site_id=site.site_id,
                    site_name=site.site_name,
                    product_name=None,
                    price=None,
                    currency=None,
                    status="не найден",
                )
            )
            continue
        best = max(hits, key=lambda h: h.match_score)
        rows.append(
            ComparePriceRow(
                site_id=site.site_id,
                site_name=site.site_name,
                product_name=best.product.name,
                price=best.product.price,
                currency=best.product.currency,
                status=best.status,
                url=best.product.url,
                match_score=best.match_score,
            )
        )
    return CompareQuerySummary(query=compare.query, rows=rows)


def best_check_per_site(row: CompareLineRow) -> dict[str, ProductCheckResult]:
    return dict(row.by_site)
