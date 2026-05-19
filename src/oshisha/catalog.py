"""Парсинг каталога oshisha.cc (Bitrix JCCatalogItem в HTML)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote_plus, urljoin

from .auth import OshishaAuth
from .query_parser import ParsedQuery, parse_query
from .vocabulary import Vocabulary, get_vocabulary, normalize_text

JCCATALOG_ITEM_RE = re.compile(r"new JCCatalogItem\((\{.*?\})\);", re.DOTALL)
WEIGHT_IN_NAME_RE = re.compile(r"(\d+)\s*(?:гр?|g)\b", re.IGNORECASE)


@dataclass
class CatalogProduct:
    """Нормализованная карточка товара из каталога."""

    id: str
    name: str
    url: str
    can_buy: bool
    max_quantity: float | None
    price: float | None
    base_price: float | None
    currency: str | None
    badges: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass
class CatalogPage:
    """Страница раздела каталога."""

    url: str
    section_title: str | None
    products: list[CatalogProduct]
    page: int | None = None
    total_pages: int | None = None


AvailabilityStatus = Literal["есть", "нет", "не найден"]


@dataclass
class ProductCheckResult:
    """Результат проверки одной позиции из списка."""

    query: str
    status: AvailabilityStatus
    matched_name: str | None = None
    product_id: str | None = None
    url: str | None = None
    price: float | None = None
    max_quantity: float | None = None
    match_score: float = 0.0
    parsed: dict[str, Any] | None = None
    pack_count: int = 1
    requested_weight_g: int | None = None
    matched_weight_g: int | None = None


def is_in_stock(product: CatalogProduct) -> bool:
    """Товар доступен к заказу."""
    if not product.can_buy:
        return False
    qty = product.max_quantity
    return qty is None or qty > 0


def _query_tokens(query: str) -> list[str]:
    tokens = [t for t in normalize_text(query).split() if len(t) >= 2]
    return tokens or [normalize_text(query)]


def _weight_in_name(name: str) -> int | None:
    m = WEIGHT_IN_NAME_RE.search(name)
    return int(m.group(1)) if m else None


def _brand_in_name(brand_key: str | None, name: str, vocab: Vocabulary) -> bool:
    if not brand_key:
        return True
    brand = vocab.brands.get(brand_key)
    if not brand:
        return True
    norm = normalize_text(name)
    return any(normalize_text(sn) in norm for sn in brand.site_names)


def score_product_match(parsed: ParsedQuery, product: CatalogProduct, vocab: Vocabulary) -> float:
    """Оценка совпадения разобранного запроса с карточкой товара."""
    name = product.name
    p_norm = normalize_text(name)

    # базовый текст для токенов
    parts = []
    if parsed.brand_key and parsed.brand_key in vocab.brands:
        parts.extend(vocab.brands[parsed.brand_key].site_names)
    for fk in parsed.flavor_keys or []:
        flavor = vocab.flavors.get(fk)
        if flavor:
            parts.extend(flavor.site_terms)
            parts.extend(flavor.aliases[:2])
    if parsed.flavor_text:
        parts.append(parsed.flavor_text)
    if not parts:
        parts.append(parsed.raw)

    query_blob = " ".join(parts)
    base = score_name_match(query_blob, name)

    for fk in parsed.flavor_keys or []:
        flavor = vocab.flavors.get(fk)
        if not flavor:
            continue
        for term in flavor.site_terms:
            if normalize_text(term) in p_norm:
                base = min(1.0, base + 0.12)

    if parsed.brand_key and not _brand_in_name(parsed.brand_key, name, vocab):
        base *= 0.35

    # «малина» без «вишня» — штраф за «малина-вишня» в названии
    q_norm = normalize_text(parsed.flavor_text or " ".join(parsed.flavor_keys or []) or parsed.raw)
    if "малина" in q_norm and "вишня" not in q_norm and "вишня" in p_norm and "деревенск" not in p_norm:
        if "малина" in p_norm and "вишня" in p_norm:
            base *= 0.55

    if parsed.weight_grams is not None:
        pw = _weight_in_name(name)
        if pw is not None:
            if pw == parsed.weight_grams:
                base = min(1.0, base + 0.18)
            else:
                base *= 0.72

    return min(1.0, base)


def score_name_match(query: str, product_name: str) -> float:
    """Оценка совпадения запроса с названием (0..1)."""
    q_norm = normalize_text(query)
    p_norm = normalize_text(product_name)
    if not q_norm or not p_norm:
        return 0.0
    if q_norm == p_norm:
        return 1.0
    if q_norm in p_norm or p_norm in q_norm:
        return 0.95

    tokens = _query_tokens(query)
    if not tokens:
        return 0.0

    hits = sum(1 for token in tokens if token in p_norm)
    score = hits / len(tokens)
    if hits == len(tokens) and len(tokens) > 1:
        score = min(1.0, score + 0.1)
    return score


def _dedupe_products(products: list[CatalogProduct]) -> list[CatalogProduct]:
    seen: set[str] = set()
    out: list[CatalogProduct] = []
    for product in products:
        if product.id in seen:
            continue
        seen.add(product.id)
        out.append(product)
    return out


def find_best_match(
    query: str,
    products: list[CatalogProduct],
    *,
    min_score: float = 0.45,
    parsed: ParsedQuery | None = None,
    vocab: Vocabulary | None = None,
) -> tuple[CatalogProduct | None, float]:
    """Найти лучшее совпадение среди товаров."""
    vocab = vocab or get_vocabulary()
    best: CatalogProduct | None = None
    best_score = 0.0
    for product in products:
        if parsed:
            score = score_product_match(parsed, product, vocab)
        else:
            score = score_name_match(query, product.name)
        if score > best_score:
            best_score = score
            best = product
    if best is None or best_score < min_score:
        return None, best_score
    return best, best_score


def _js_object_to_dict(js_object: str) -> dict[str, Any]:
    """Конвертация JS-объекта из JCCatalogItem в dict (одинарные кавычки)."""
    return json.loads(js_object.replace("'", '"'))


def _parse_price(product: dict[str, Any]) -> tuple[float | None, float | None, str | None]:
    prices = product.get("ITEM_PRICES") or []
    if not prices:
        return None, None, None
    row = prices[0]
    return (
        _to_float(row.get("PRICE")),
        _to_float(row.get("BASE_PRICE")),
        row.get("CURRENCY"),
    )


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_catalog_html(html: str, *, page_url: str = "") -> CatalogPage:
    """
    Извлечь товары из HTML страницы каталога.

    Данные берутся из inline-скриптов `new JCCatalogItem({...})` (стандарт Bitrix).
    """
    products: list[CatalogProduct] = []
    seen_ids: set[str] = set()

    for block in JCCATALOG_ITEM_RE.finditer(html):
        try:
            data = _js_object_to_dict(block.group(1))
        except json.JSONDecodeError:
            continue

        product = data.get("PRODUCT") or {}
        product_id = str(product.get("ID", ""))
        if not product_id or product_id in seen_ids:
            continue
        seen_ids.add(product_id)

        price, base_price, currency = _parse_price(product)
        detail_url = product.get("DETAIL_PAGE_URL") or ""
        if detail_url and not detail_url.startswith("http"):
            detail_url = urljoin(page_url or "https://oshisha.cc", detail_url)

        products.append(
            CatalogProduct(
                id=product_id,
                name=str(product.get("NAME", "")),
                url=detail_url,
                can_buy=bool(product.get("CAN_BUY")),
                max_quantity=_to_float(product.get("MAX_QUANTITY")),
                price=price,
                base_price=base_price,
                currency=currency,
                raw={"catalog": data, "product": product},
            )
        )

    title_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
    page_match = re.search(r"bx-active[^>]*><span[^>]*>(\d+)</span>", html)
    page_nums = [int(x) for x in re.findall(r"bx-pagination-container.*?>(\d+)<", html, re.DOTALL)]
    # fallback: all pagination numbers
    if not page_nums:
        page_nums = [int(x) for x in re.findall(r'class="bx-pagination[^"]*".*?>(\d+)<', html)]

    return CatalogPage(
        url=page_url,
        section_title=title_match.group(1).strip() if title_match else None,
        products=products,
        page=int(page_match.group(1)) if page_match else None,
        total_pages=max(page_nums) if page_nums else None,
    )


class OshishaCatalog:
    """Запрос страниц каталога с сессией Oshisha."""

    def __init__(
        self,
        auth: OshishaAuth,
        vocab: Vocabulary | None = None,
        *,
        load_vocab: bool = True,
    ) -> None:
        self.auth = auth
        self.vocab = vocab if vocab is not None else (get_vocabulary() if load_vocab else None)

    def fetch_section(
        self,
        section_path: str,
        *,
        page: int | None = None,
    ) -> CatalogPage:
        """
        Загрузить раздел каталога.

        Пример: `/catalog/nash_1/` или `catalog/nash_1/`
        """
        path = section_path if section_path.startswith("/") else f"/{section_path}"
        if not path.endswith("/"):
            path += "/"
        if page and page > 1:
            path = f"{path}?PAGEN_1={page}"

        resp = self.auth.get(path)
        resp.raise_for_status()
        return parse_catalog_html(resp.text, page_url=str(resp.url))

    def fetch_section_all_pages(self, section_path: str) -> list[CatalogProduct]:
        """Загрузить все страницы раздела каталога."""
        first = self.fetch_section(section_path)
        products = list(first.products)
        total = first.total_pages or 1
        for page_num in range(2, total + 1):
            page = self.fetch_section(section_path, page=page_num)
            products.extend(page.products)
        return products

    def search(self, query: str) -> CatalogPage:
        """Поиск по сайту: /catalog/?q=..."""
        path = f"/catalog/?q={quote_plus(query.strip())}"
        resp = self.auth.get(path)
        resp.raise_for_status()
        return parse_catalog_html(resp.text, page_url=str(resp.url))

    def _gather_candidates(self, parsed: ParsedQuery) -> list[CatalogProduct]:
        products: list[CatalogProduct] = []
        search_queries = self.vocab.build_search_terms(
            brand_key=parsed.brand_key,
            flavor_keys=parsed.flavor_keys or [],
            flavor_text=parsed.flavor_text,
            weight=parsed.weight_grams,
        )
        if not search_queries:
            search_queries = [parsed.raw]

        for search_q in search_queries:
            page = self.search(search_q)
            products.extend(page.products)

        product, score = find_best_match(
            parsed.raw, products, min_score=0.0, parsed=parsed, vocab=self.vocab
        )
        section = self.vocab.section_for(parsed.brand_key, parsed.flavor_keys or [])
        if (product is None or score < 0.65) and section:
            section_page = self.fetch_section(section)
            products.extend(section_page.products)
            total = section_page.total_pages or 1
            for page_num in range(2, min(total + 1, 6)):
                products.extend(self.fetch_section(section, page=page_num).products)

        return _dedupe_products(products)

    def check_product(self, query: str, *, min_score: float = 0.48) -> ProductCheckResult:
        """Проверить одну позицию: разбор запроса + поиск + наличие."""
        parsed = parse_query(query, self.vocab)
        products = self._gather_candidates(parsed)
        product, score = find_best_match(
            query, products, min_score=min_score, parsed=parsed, vocab=self.vocab
        )

        parsed_info = {
            "brand": parsed.brand_display,
            "flavors": parsed.flavor_keys,
            "weight_g": parsed.weight_grams,
            "pack_count": parsed.pack_count,
            "summary": parsed.summary(),
        }

        if product is None:
            return ProductCheckResult(
                query=query,
                status="не найден",
                match_score=score,
                parsed=parsed_info,
                pack_count=parsed.pack_count,
            )

        in_stock = is_in_stock(product)
        matched_w = _weight_in_name(product.name)
        req_w = parsed.weight_grams

        if not in_stock:
            status: AvailabilityStatus = "нет"
        elif req_w and matched_w and req_w != matched_w:
            status = "есть"  # другая фасовка — см. matched_weight_g
        else:
            status = "есть"

        return ProductCheckResult(
            query=query,
            status=status,
            matched_name=product.name,
            product_id=product.id,
            url=product.url,
            price=product.price,
            max_quantity=product.max_quantity,
            match_score=round(score, 3),
            parsed=parsed_info,
            pack_count=parsed.pack_count,
            requested_weight_g=req_w,
            matched_weight_g=matched_w,
        )

    def check_products(
        self,
        names: list[str],
        *,
        min_score: float = 0.48,
    ) -> list[ProductCheckResult]:
        """Проверить список названий из бота."""
        results: list[ProductCheckResult] = []
        for name in names:
            line = name.strip()
            if not line or line.startswith("#"):
                continue
            results.append(self.check_product(line, min_score=min_score))
        return results
