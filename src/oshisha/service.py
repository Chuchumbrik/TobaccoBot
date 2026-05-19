"""Сервис Oshisha: сессия + каталог для бота и скриптов."""

from __future__ import annotations

import os
from pathlib import Path

from .auth import OshishaAuth, OshishaAuthError
from .catalog import OshishaCatalog, ProductCheckResult
from .flavor_search import FlavorSearchResult, search_by_flavor

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
