"""Общие типы для мульти-сайтового слоя (поверх oshisha.*)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Flag, auto

from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchResult


class SiteCapability(Flag):
    """Что умеет провайдер конкретного сайта."""

    SEARCH = auto()
    CHECK = auto()
    CART = auto()
    ADVISE = auto()  # LLM/таксономия привязаны к vocab сайта


DEFAULT_CAPABILITIES = SiteCapability.SEARCH | SiteCapability.CHECK | SiteCapability.CART


@dataclass(frozen=True)
class SiteInfo:
    site_id: str
    display_name: str
    base_url: str
    capabilities: SiteCapability = DEFAULT_CAPABILITIES


@dataclass
class SiteFlavorSearch:
    site_id: str
    site_name: str
    result: FlavorSearchResult | None = None
    error: str | None = None


@dataclass
class SiteCheckBatch:
    site_id: str
    site_name: str
    results: list[ProductCheckResult] = field(default_factory=list)
    error: str | None = None


@dataclass
class CompareSearchResult:
    """Поиск по вкусу на нескольких сайтах."""

    query: str
    limit: int
    sites: list[SiteFlavorSearch] = field(default_factory=list)

    @property
    def ok_sites(self) -> list[SiteFlavorSearch]:
        return [s for s in self.sites if s.error is None and s.result is not None]

    @property
    def failed_sites(self) -> list[SiteFlavorSearch]:
        return [s for s in self.sites if s.error is not None]


@dataclass
class CompareLineRow:
    """Одна строка списка — статус на каждом сайте."""

    query: str
    by_site: dict[str, ProductCheckResult] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass
class CompareListResult:
    """Проверка списка на нескольких сайтах."""

    lines: list[CompareLineRow] = field(default_factory=list)
    site_ids: list[str] = field(default_factory=list)
    site_names: dict[str, str] = field(default_factory=dict)


@dataclass
class ComparePriceRow:
    """Лучшее совпадение по запросу на сайте (для сводной таблицы цен)."""

    site_id: str
    site_name: str
    product_name: str | None
    price: float | None
    currency: str | None
    status: str
    url: str | None = None
    match_score: float = 0.0


@dataclass
class CompareQuerySummary:
    """Сводка по одному запросу: лучшие предложения по сайтам."""

    query: str
    rows: list[ComparePriceRow] = field(default_factory=list)

    @property
    def cheapest_in_stock(self) -> ComparePriceRow | None:
        in_stock = [
            r
            for r in self.rows
            if r.status == "есть" and r.price is not None
        ]
        if not in_stock:
            return None
        return min(in_stock, key=lambda r: r.price)  # type: ignore[arg-type]
