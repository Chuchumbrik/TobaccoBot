"""Сервис Oshisha: сессия + каталог для бота и скриптов."""

from __future__ import annotations

import os
from pathlib import Path

from .auth import OshishaAuth, OshishaAuthError
from .cart import CartAddBatchResult, CartAddResult, CartView, OshishaCart
from .catalog import OshishaCatalog, ProductCheckResult
from . import catalog_cache
from .catalog_snapshot import CatalogSnapshot
from .flavor_search import FlavorSearchHit, FlavorSearchResult

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SESSION = ROOT / "data" / "sessions" / "oshisha.json"


class OshishaService:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        email: str | None = None,
        password: str | None = None,
        session_path: Path | None = None,
    ) -> None:
        self.base_url = base_url or os.environ.get("OSHISHA_BASE_URL", "https://oshisha.cc")
        self.email = email or os.environ.get("OSHISHA_EMAIL", "")
        self.password = password or os.environ.get("OSHISHA_PASSWORD", "")
        self.session_path = session_path or DEFAULT_SESSION
        self._auth: OshishaAuth | None = None
        self._catalog: OshishaCatalog | None = None

    def _ensure_login(self) -> OshishaCatalog:
        if self._catalog is not None:
            return self._catalog

        self._auth = OshishaAuth(self.base_url, session_file=self.session_path)
        if not self._auth.is_authenticated:
            if not self.email or not self.password:
                raise OshishaAuthError(
                    "Нет сессии Oshisha. Задайте OSHISHA_EMAIL и OSHISHA_PASSWORD в .env"
                )
            self._auth.login_email(self.email, self.password)

        self._catalog = OshishaCatalog(self._auth)
        return self._catalog

    def check_list(self, lines: list[str]) -> list[ProductCheckResult]:
        catalog = self._ensure_login()
        return catalog.check_products(lines)

    def add_to_cart(self, lines: list[str]) -> CartAddBatchResult:
        """Найти позиции по строкам и добавить в корзину на сайте."""
        catalog = self._ensure_login()
        if self._auth is None:
            raise OshishaAuthError("Сессия не инициализирована")
        return OshishaCart(self._auth).add_queries(catalog, lines)

    def add_checks_to_cart(
        self,
        checks: list[ProductCheckResult],
        *,
        indices: list[int] | None = None,
    ) -> CartAddBatchResult:
        """Добавить в корзину уже проверенные позиции (по индексам или все)."""
        self._ensure_login()
        if self._auth is None:
            raise OshishaAuthError("Сессия не инициализирована")
        if indices is None:
            selected = checks
        else:
            selected = [checks[i] for i in indices if 0 <= i < len(checks)]
        queries = [c.query for c in selected]
        return OshishaCart(self._auth).add_checks(selected, queries=queries)

    def add_flavor_hits_to_cart(
        self,
        hits: list[FlavorSearchHit],
        indices: list[int],
    ) -> CartAddBatchResult:
        """Добавить в корзину позиции из результатов поиска по вкусу."""
        catalog = self._ensure_login()
        if self._auth is None:
            raise OshishaAuthError("Сессия не инициализирована")

        # CartAddResult уже импортирован на уровне модуля

        verify = os.environ.get("CATALOG_VERIFY_CART", "1").strip().lower() not in (
            "0",
            "false",
            "no",
        )
        pending: list[tuple] = []
        skipped: list[CartAddResult] = []
        for i in indices:
            if i < 0 or i >= len(hits):
                continue
            hit = hits[i]
            label = hit.product.name
            if hit.flavor_query:
                label = f"{hit.flavor_query} → {hit.product.name}"
            product = hit.product
            if verify:
                product, ok = catalog_cache.verify_product_for_cart(catalog, hit.product)
                if not ok:
                    skipped.append(
                        CartAddResult(
                            query=label,
                            success=False,
                            message="нет в наличии (проверка перед корзиной)",
                            matched_name=hit.product.name,
                            product_id=hit.product.id,
                        )
                    )
                    continue
            pending.append((product, label, 1))

        if not pending:
            return CartAddBatchResult(items=skipped)

        batch = OshishaCart(self._auth).add_from_products(pending)
        batch.items = skipped + batch.items
        return batch

    def view_cart(self) -> CartView:
        """Содержимое корзины на сайте."""
        self._ensure_login()
        if self._auth is None:
            raise OshishaAuthError("Сессия не инициализирована")
        return OshishaCart(self._auth).fetch_cart()

    def search_flavor(
        self,
        query: str,
        *,
        limit: int = 15,
        in_stock_only: bool = False,
    ) -> FlavorSearchResult:
        catalog = self._ensure_login()
        return catalog_cache.search_flavor(
            catalog,
            query,
            limit=limit,
            in_stock_only=in_stock_only,
        )

    def warmup_catalog(self, *, force: bool = False) -> CatalogSnapshot:
        """Полный снимок каталога для локального поиска."""
        catalog = self._ensure_login()
        return catalog_cache.refresh(catalog, force=force)

    def close(self) -> None:
        if self._auth:
            self._auth.close()
            self._auth = None
            self._catalog = None
        catalog_cache.invalidate()
