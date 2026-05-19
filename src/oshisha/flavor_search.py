"""Поиск табака по вкусу (все бренды или один)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .catalog import (
    CatalogProduct,
    _dedupe_products,
    _weight_in_name,
    is_in_stock,
    parse_query,
    score_name_match,
    score_product_match,
)
from .query_parser import ParsedQuery
from .vocabulary import Vocabulary, normalize_text


@dataclass
class FlavorSearchHit:
    """Один товар в результатах поиска по вкусу."""

    flavor_query: str
    product: CatalogProduct
    brand_key: str | None
    brand_display: str | None
    status: str  # есть | нет
    match_score: float
    requested_weight_g: int | None = None
    matched_weight_g: int | None = None

    @property
    def weight_note(self) -> str | None:
        if (
            self.requested_weight_g
            and self.matched_weight_g
            and self.requested_weight_g != self.matched_weight_g
        ):
            return f"{self.matched_weight_g}г (запрошено {self.requested_weight_g}г)"
        return None


@dataclass
class FlavorSearchResult:
    """Результат поиска по вкусу."""

    query: str
    parsed: ParsedQuery
    hits: list[FlavorSearchHit] = field(default_factory=list)
    flavor_keys_matched: list[str] = field(default_factory=list)

    @property
    def in_stock_count(self) -> int:
        return sum(1 for h in self.hits if h.status == "есть")


def _brand_key_from_product(name: str, vocab: Vocabulary) -> tuple[str | None, str | None]:
    norm = normalize_text(name)
    for key, brand in vocab.brands.items():
        for site_name in brand.site_names:
            if normalize_text(site_name) in norm:
                return key, brand.display
    return None, None


def _flavor_only_parsed(query: str, vocab: Vocabulary) -> ParsedQuery:
    """Разбор запроса как «только вкус» (бренд опционален в конце через «| бренд»)."""
    raw = query.strip()
    brand_suffix = None
    if "|" in raw:
        parts = raw.split("|", 1)
        raw = parts[0].strip()
        brand_suffix = parts[1].strip() if len(parts) > 1 else None

    parsed = parse_query(raw, vocab)
    if brand_suffix:
        bk, _ = vocab.match_brand(brand_suffix)
        if bk:
            parsed.brand_key = bk
            parsed.brand_display = vocab.brands[bk].display
    elif parsed.brand_key and not parsed.flavor_keys and parsed.flavor_text:
        # «малина сарма» — бренд мог распознаться как начало; оставляем flavor_text
        pass
    return parsed


def search_by_flavor(
    catalog: object,
    query: str,
    *,
    limit: int = 15,
    min_score: float = 0.42,
    in_stock_only: bool = False,
) -> FlavorSearchResult:
    """
    Найти табак по вкусу на Oshisha.

    Примеры:
      «малина» — все бренды с малиной
      «арбуз дыня 200» — с фильтром по граммовке
      «кокос | must have» — только MustHave

    catalog: OshishaCatalog (передаём объект, чтобы не импортировать циклом).
    """
    vocab: Vocabulary = catalog.vocab  # type: ignore[attr-defined]
    parsed = _flavor_only_parsed(query, vocab)

    if not parsed.flavor_keys and parsed.flavor_text:
        parsed.flavor_keys = vocab.match_flavors(parsed.flavor_text, brand_key=parsed.brand_key)

    flavor_keys = parsed.flavor_keys
    if not flavor_keys and parsed.flavor_text:
        flavor_keys = []

    search_queries = vocab.build_search_terms(
        brand_key=parsed.brand_key,
        flavor_keys=flavor_keys,
        flavor_text=parsed.flavor_text or query,
        weight=parsed.weight_grams,
    )
    if not search_queries:
        search_queries = [query.strip()]

    products: list[CatalogProduct] = []
    for sq in search_queries[:10]:
        page = catalog.search(sq)  # type: ignore[attr-defined]
        products.extend(page.products)

    if parsed.brand_key:
        section = vocab.section_for(parsed.brand_key, flavor_keys)
        if section:
            page = catalog.fetch_section(section)  # type: ignore[attr-defined]
            products.extend(page.products)

    products = _dedupe_products(products)

    scored: list[tuple[float, CatalogProduct, str | None, str | None]] = []
    for product in products:
        score = score_product_match(parsed, product, vocab)
        if score < min_score:
            flavor_blob = " ".join(
                vocab.flavors[fk].display for fk in flavor_keys if fk in vocab.flavors
            ) or (parsed.flavor_text or query)
            score = max(score, score_name_match(flavor_blob, product.name) * 0.9)
        if score < min_score:
            continue
        bk, bd = _brand_key_from_product(product.name, vocab)
        scored.append((score, product, bk, bd))

    scored.sort(key=lambda x: (-x[0], x[1].name))

    hits: list[FlavorSearchHit] = []
    seen_ids: set[str] = set()
    for score, product, bk, bd in scored:
        if product.id in seen_ids:
            continue
        seen_ids.add(product.id)

        in_stock = is_in_stock(product)
        if in_stock_only and not in_stock:
            continue

        matched_w = _weight_in_name(product.name)
        status = "есть" if in_stock else "нет"

        hits.append(
            FlavorSearchHit(
                flavor_query=query,
                product=product,
                brand_key=bk,
                brand_display=bd,
                status=status,
                match_score=round(score, 3),
                requested_weight_g=parsed.weight_grams,
                matched_weight_g=matched_w,
            )
        )
        if len(hits) >= limit:
            break

    hits.sort(key=lambda h: (h.status != "есть", -h.match_score))

    return FlavorSearchResult(
        query=query,
        parsed=parsed,
        hits=hits,
        flavor_keys_matched=flavor_keys,
    )
