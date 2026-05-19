"""Сервис Oshisha: сессия + каталог для бота и скриптов."""

from __future__ import annotations

import os
from pathlib import Path

from .auth import OshishaAuth, OshishaAuthError
from .cart import CartAddBatchResult, CartView, OshishaCart
from .catalog import OshishaCatalog, ProductCheckResult
from .flavor_search import FlavorSearchHit, FlavorSearchResult, search_by_flavor

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
        self._ensure_login()
        if self._auth is None:
            raise OshishaAuthError("Сессия не инициализирована")
        items: list[tuple] = []
        for i in indices:
            if i < 0 or i >= len(hits):
                continue
            hit = hits[i]
            label = hit.product.name
            if hit.flavor_query:
                label = f"{hit.flavor_query} → {hit.product.name}"
            items.append((hit.product, label, 1))
        return OshishaCart(self._auth).add_from_products(items)

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
        return search_by_flavor(
            catalog,
            query,
            limit=limit,
            in_stock_only=in_stock_only,
        )

    def close(self) -> None:
        if self._auth:
            self._auth.close()
            self._auth = None
            self._catalog = None
