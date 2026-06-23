"""Провайдер oshisha.cc (текущая реализация)."""

from __future__ import annotations

from oshisha.cart import CartAddBatchResult, CartView
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult
from oshisha.service import OshishaService

from shops.types import SiteCapability, SiteInfo


class OshishaShopProvider:
    site_id = "oshisha"

    def __init__(self, service: OshishaService | None = None) -> None:
        self._svc = service or OshishaService()
        self._info = SiteInfo(
            site_id=self.site_id,
            display_name="Oshisha",
            base_url=self._svc.base_url,
            capabilities=(
                SiteCapability.SEARCH
                | SiteCapability.CHECK
                | SiteCapability.CART
                | SiteCapability.ADVISE
            ),
        )

    @property
    def info(self) -> SiteInfo:
        return self._info

    @property
    def inner(self) -> OshishaService:
        """Доступ к низкоуровневому сервису (скрипты, отладка)."""
        return self._svc

    def check_list(self, lines: list[str]) -> list[ProductCheckResult]:
        return self._svc.check_list(lines)

    def search_flavor(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
    ) -> FlavorSearchResult:
        return self._svc.search_flavor(
            query, limit=limit, in_stock_only=in_stock_only
        )

    def warmup_catalog(self, *, force: bool = False):
        return self._svc.warmup_catalog(force=force)

    def add_to_cart(self, lines: list[str]) -> CartAddBatchResult:
        return self._svc.add_to_cart(lines)

    def add_checks_to_cart(
        self,
        checks: list[ProductCheckResult],
        *,
        indices: list[int] | None = None,
    ) -> CartAddBatchResult:
        return self._svc.add_checks_to_cart(checks, indices=indices)

    def add_flavor_hits_to_cart(
        self,
        hits: list[FlavorSearchHit],
        indices: list[int],
    ) -> CartAddBatchResult:
        return self._svc.add_flavor_hits_to_cart(hits, indices)

    def view_cart(self) -> CartView:
        return self._svc.view_cart()

    def close(self) -> None:
        self._svc.close()

    def has_capability(self, cap: SiteCapability) -> bool:
        return cap in self.info.capabilities
