"""Контракт провайдера магазина — реализуйте для каждого нового сайта."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from oshisha.cart import CartAddBatchResult, CartView
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

from .types import SiteCapability, SiteInfo


@runtime_checkable
class ShopProvider(Protocol):
    """Адаптер одного оптового сайта."""

    @property
    def info(self) -> SiteInfo: ...

    def check_list(self, lines: list[str]) -> list[ProductCheckResult]: ...

    def search_flavor(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
    ) -> FlavorSearchResult: ...

    def add_to_cart(self, lines: list[str]) -> CartAddBatchResult: ...

    def add_checks_to_cart(
        self,
        checks: list[ProductCheckResult],
        *,
        indices: list[int] | None = None,
    ) -> CartAddBatchResult: ...

    def add_flavor_hits_to_cart(
        self,
        hits: list[FlavorSearchHit],
        indices: list[int],
    ) -> CartAddBatchResult: ...

    def view_cart(self) -> CartView: ...

    def close(self) -> None: ...

    def has_capability(self, cap: SiteCapability) -> bool:
        return cap in self.info.capabilities
