"""Шаблон провайдера для нового сайта (тесты и отладка сравнения)."""

from __future__ import annotations

from oshisha.cart import CartAddBatchResult, CartView
from oshisha.catalog import CatalogProduct, ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult
from oshisha.query_parser import parse_query
from oshisha.vocabulary import get_vocabulary

from shops.types import SiteCapability, SiteInfo


class StubShopProvider:
    """
    Заглушка: имитирует второй магазин без HTTP.

    Скопируйте файл, переименуйте класс, реализуйте HTTP/парсинг каталога,
    зарегистрируйте в providers/__init__.py через register_site().
    """

    def __init__(
        self,
        *,
        site_id: str = "stub",
        display_name: str = "Stub Shop",
        base_url: str = "https://example.invalid",
    ) -> None:
        self._info = SiteInfo(
            site_id=site_id,
            display_name=display_name,
            base_url=base_url,
            capabilities=SiteCapability.SEARCH | SiteCapability.CHECK,
        )

    @property
    def info(self) -> SiteInfo:
        return self._info

    def warmup_catalog(self, *, force: bool = False):
        from oshisha import catalog_cache
        from shops.snapshot_build import build_snapshot_for_provider

        return catalog_cache.refresh_site(
            self.info.site_id,
            lambda: build_snapshot_for_provider(self),
            force=force,
        )

    def _live_search(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
    ) -> FlavorSearchResult:
        parsed = parse_query(query)
        product = CatalogProduct(
            id="stub-1",
            name=f"[{self.info.display_name}] {query} 200гр",
            url=self.info.base_url,
            can_buy=True,
            max_quantity=10,
            price=999.0,
            base_price=999.0,
            currency="RUB",
        )
        hit = FlavorSearchHit(
            flavor_query=query,
            product=product,
            brand_key=None,
            brand_display=None,
            status="есть",
            match_score=0.85,
            requested_weight_g=parsed.weight_grams,
            matched_weight_g=200,
        )
        hits = [hit]
        if in_stock_only:
            hits = [h for h in hits if h.status == "есть"]
        return FlavorSearchResult(
            query=query,
            parsed=parsed,
            hits=hits[:limit],
        )

    def check_list(self, lines: list[str]) -> list[ProductCheckResult]:
        from oshisha import catalog_cache

        vocab = get_vocabulary()
        out: list[ProductCheckResult] = []
        sid = self.info.site_id
        for line in lines:
            snap_hit = catalog_cache.check_product_snapshot(
                sid, line, vocab, min_score=0.35
            )
            if snap_hit is not None:
                out.append(snap_hit)
                continue
            out.append(
                ProductCheckResult(
                    query=line,
                    status="есть",
                    matched_name=f"[{self.info.display_name}] {line}",
                    product_id="stub-1",
                    url=self.info.base_url,
                    price=999.0,
                    match_score=0.9,
                )
            )
        return out

    def search_flavor(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
    ) -> FlavorSearchResult:
        from oshisha import catalog_cache

        vocab = get_vocabulary()
        return catalog_cache.search_flavor_cached(
            self.info.site_id,
            query,
            vocab,
            lambda: self._live_search(
                query, limit=limit, in_stock_only=in_stock_only
            ),
            limit=limit,
            in_stock_only=in_stock_only,
        )

    def add_to_cart(self, lines: list[str]) -> CartAddBatchResult:
        raise NotImplementedError(f"{self.info.site_id}: корзина не реализована")

    def add_checks_to_cart(
        self,
        checks: list[ProductCheckResult],
        *,
        indices: list[int] | None = None,
    ) -> CartAddBatchResult:
        raise NotImplementedError(f"{self.info.site_id}: корзина не реализована")

    def add_flavor_hits_to_cart(
        self,
        hits: list[FlavorSearchHit],
        indices: list[int],
    ) -> CartAddBatchResult:
        raise NotImplementedError(f"{self.info.site_id}: корзина не реализована")

    def view_cart(self) -> CartView:
        raise NotImplementedError(f"{self.info.site_id}: корзина не реализована")

    def close(self) -> None:
        pass

    def has_capability(self, cap: SiteCapability) -> bool:
        return cap in self.info.capabilities
