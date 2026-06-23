"""Единая точка доступа: primary-сайт для бота + мульти-сайт и сравнение."""

from __future__ import annotations

import os
from typing import Any

from oshisha.cart import CartAddBatchResult, CartView
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

from .compare import compare_list, compare_search
from .protocol import ShopProvider
from .registry import create_provider, create_providers, parse_site_ids_from_env
from .types import CompareListResult, CompareSearchResult, SiteCapability

# Регистрация встроенных провайдеров
from shops import providers as _providers  # noqa: F401


class ShopHub:
    """
    Фасад для бота и скриптов.

    Методы check_list / search_flavor / cart делегируются primary-сайту
    (первый в SHOP_SITES, по умолчанию oshisha).
    compare_* — по всем активным сайтам из SHOP_SITES.
    """

    def __init__(
        self,
        *,
        site_ids: list[str] | None = None,
        providers: list[ShopProvider] | None = None,
    ) -> None:
        if providers is not None:
            self._providers = list(providers)
            self._site_ids = [p.info.site_id for p in providers]
        else:
            self._site_ids = site_ids or parse_site_ids_from_env()
            self._providers = create_providers(self._site_ids)
        if not self._providers:
            raise ValueError("Нет активных провайдеров магазинов")
        self._primary = self._providers[0]
        self._by_id = {p.info.site_id: p for p in self._providers}

    @classmethod
    def from_env(cls) -> ShopHub:
        return cls()

    @property
    def primary_site_id(self) -> str:
        return self._primary.info.site_id

    @property
    def primary(self) -> ShopProvider:
        return self._primary

    @property
    def site_ids(self) -> list[str]:
        return list(self._site_ids)

    def provider(self, site_id: str) -> ShopProvider:
        sid = site_id.strip().lower()
        p = self._by_id.get(sid)
        if p is None:
            raise KeyError(
                f"Сайт {site_id!r} не в активном списке {self._site_ids}"
            )
        return p

    def list_sites(self) -> list[tuple[str, str]]:
        return [(p.info.site_id, p.info.display_name) for p in self._providers]

    # ── Делегирование primary (совместимость с OshishaService) ─────────────

    def check_list(self, lines: list[str]) -> list[ProductCheckResult]:
        return self._primary.check_list(lines)

    def search_flavor(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
    ) -> FlavorSearchResult:
        return self._primary.search_flavor(
            query, limit=limit, in_stock_only=in_stock_only
        )

    def warmup_catalog(self, *, force: bool = False):
        """Прогрев снимка primary-сайта."""
        return self.warmup_all_catalogs(force=force)[self.primary_site_id]

    def warmup_all_catalogs(self, *, force: bool = False) -> dict[str, object]:
        """Прогрев снимков для всех активных сайтов."""
        from oshisha import catalog_cache
        from oshisha.catalog_snapshot import CatalogSnapshot
        from shops.snapshot_build import build_snapshot_for_provider

        results: dict[str, CatalogSnapshot] = {}
        for provider in self._providers:
            sid = provider.info.site_id
            if hasattr(provider, "warmup_catalog"):
                results[sid] = provider.warmup_catalog(force=force)
            else:
                results[sid] = catalog_cache.refresh_site(
                    sid,
                    lambda p=provider: build_snapshot_for_provider(p),
                    force=force,
                )
        return results

    def add_to_cart(self, lines: list[str]) -> CartAddBatchResult:
        return self._primary.add_to_cart(lines)

    def add_checks_to_cart(
        self,
        checks: list[ProductCheckResult],
        *,
        indices: list[int] | None = None,
    ) -> CartAddBatchResult:
        return self._primary.add_checks_to_cart(checks, indices=indices)

    def add_flavor_hits_to_cart(
        self,
        hits: list[FlavorSearchHit],
        indices: list[int],
    ) -> CartAddBatchResult:
        return self._primary.add_flavor_hits_to_cart(hits, indices)

    def view_cart(self) -> CartView:
        return self._primary.view_cart()

    def close(self) -> None:
        for p in self._providers:
            try:
                p.close()
            except Exception:
                pass

    # ── Мульти-сайт ────────────────────────────────────────────────────────

    def search_all_sites(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
        site_ids: list[str] | None = None,
    ) -> dict[str, FlavorSearchResult]:
        """Поиск на выбранных или всех активных сайтах."""
        providers = self._resolve_providers(site_ids)
        out: dict[str, FlavorSearchResult] = {}
        for p in providers:
            if not p.has_capability(SiteCapability.SEARCH):
                continue
            out[p.info.site_id] = p.search_flavor(
                query, limit=limit, in_stock_only=in_stock_only
            )
        return out

    def compare_search_flavor(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
        site_ids: list[str] | None = None,
    ) -> CompareSearchResult:
        providers = self._resolve_providers(site_ids)
        return compare_search(
            providers,
            query,
            limit=limit,
            in_stock_only=in_stock_only,
        )

    def compare_check_list(
        self,
        lines: list[str],
        *,
        site_ids: list[str] | None = None,
    ) -> CompareListResult:
        providers = self._resolve_providers(site_ids)
        return compare_list(providers, lines)

    def _resolve_providers(
        self, site_ids: list[str] | None
    ) -> list[ShopProvider]:
        if not site_ids:
            return self._providers
        return [self.provider(sid) for sid in site_ids]

    def __getattr__(self, name: str) -> Any:
        """Доступ к полям inner OshishaService: hub.base_url и т.д."""
        inner = getattr(self._primary, "inner", None)
        if inner is not None and hasattr(inner, name):
            return getattr(inner, name)
        raise AttributeError(name)
