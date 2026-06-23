"""Парсинг каталога oshisha.cc (Bitrix JCCatalogItem в HTML)."""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote_plus, urljoin

from .auth import OshishaAuth
from .query_parser import ParsedQuery, parse_query
from .vocabulary import Vocabulary, get_vocabulary, normalize_text

JCCATALOG_ITEM_RE = re.compile(r"new JCCatalogItem\((\{.*?\})\);", re.DOTALL)
WEIGHT_IN_NAME_RE = re.compile(r"(\d+)\s*(?:гр?|g)\b", re.IGNORECASE)

# Пары цвет-антоним: «красная смородина» не должна матчить «чёрная смородина»
_COLOR_ANTONYMS: dict[str, frozenset[str]] = {
    "красн": frozenset(["черн"]),
    "черн": frozenset(["красн"]),
}


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
            t_norm = normalize_text(term)
            t_words = t_norm.split()
            if t_norm in p_norm:
                # Короткий term (1–2 слова) прямо в названии — сильный сигнал вкуса
                if len(t_words) <= 2:
                    t_score = score_name_match(term, name)
                    base = min(1.0, max(base + 0.12, t_score * 0.88 if t_score >= 0.65 else base + 0.12))
                else:
                    base = min(1.0, base + 0.12)
            elif len(t_words) <= 3:
                # Term не вошёл как подстрока, но частично совпадает (напр. «Strawberry Nectar» vs «Duft Strawberry»)
                t_score = score_name_match(term, name)
                if t_score >= 0.45:
                    base = min(1.0, max(base, t_score * 0.85))

    if parsed.brand_key and not _brand_in_name(parsed.brand_key, name, vocab):
        base *= 0.35

    # «малина» без «вишня» — штраф за «малина-вишня» в названии
    q_norm = normalize_text(parsed.flavor_text or " ".join(parsed.flavor_keys or []) or parsed.raw)
    if "малина" in q_norm and "вишня" not in q_norm and "вишня" in p_norm and "деревенск" not in p_norm:
        if "малина" in p_norm and "вишня" in p_norm:
            base *= 0.55

    # Штраф за конфликт цвета («красная смородина» не должна матчить «чёрная смородина»)
    q_raw_norm = normalize_text(parsed.raw)
    for color_q, antonyms in _COLOR_ANTONYMS.items():
        if color_q in q_raw_norm and all(a not in q_raw_norm for a in antonyms):
            if any(a in p_norm for a in antonyms):
                base *= 0.35
                break

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


NON_TOBACCO_NAME_RE = re.compile(
    r"\b(уголь|кальян|колба|чаша|чашка|шланг|калауд|kaloud|плитка|щипцы|"
    r"мундштук|фольга|чубук|шахта|блюдце|клипса)\b",
    re.IGNORECASE,
)


def _flavor_evidence_tokens(
    parsed: ParsedQuery, vocab: Vocabulary
) -> tuple[set[str], set[str]]:
    """Токены искомого вкуса: (однословные, многословные-фразы)."""
    singles: set[str] = set()
    phrases: set[str] = set()

    def add(text: str) -> None:
        norm = normalize_text(text)
        if not norm:
            return
        words = norm.split()
        if len(words) == 1:
            if len(norm) >= 3:
                singles.add(norm)
        else:
            phrases.add(norm)

    for fk in parsed.flavor_keys or []:
        flavor = vocab.flavors.get(fk)
        if not flavor:
            continue
        add(flavor.display)
        for term in flavor.site_terms:
            add(term)
        for alias in flavor.aliases:
            add(alias)

    if parsed.flavor_text:
        for word in normalize_text(parsed.flavor_text).split():
            if len(word) >= 3:
                singles.add(word)

    return singles, phrases


def has_flavor_evidence(
    parsed: ParsedQuery, product_name: str, vocab: Vocabulary
) -> bool:
    """Есть ли в названии товара след искомого вкуса (а не только бренда).

    Защищает от «совпадение только по бренду»: когда нужного вкуса в каталоге
    нет, скоринг всё равно поднимает любой товар того же бренда выше порога.
    """
    if not (parsed.flavor_keys or parsed.flavor_text):
        return True  # вкус не задан — гейтить нечего

    singles, phrases = _flavor_evidence_tokens(parsed, vocab)
    if not singles and not phrases:
        return True

    pn = normalize_text(product_name)
    if any(phrase in pn for phrase in phrases):
        return True
    pwords = set(pn.split())
    for tok in singles:
        if tok in pwords:
            return True
        # морфология: «малина» → «малиновый», но только для длинных основ
        if len(tok) >= 5 and tok in pn:
            return True
    return False


def filter_by_flavor_evidence(
    parsed: ParsedQuery,
    products: list[CatalogProduct],
    vocab: Vocabulary,
) -> list[CatalogProduct]:
    """Оставить только кандидатов с признаком искомого вкуса.

    Если задан вкус и есть хотя бы один товар с его следом — вернуть только их.
    Если ни одного — вернуть пустой список (значит, точного вкуса нет в наличии).
    Если вкус не задан — вернуть всё как есть.
    """
    if not (parsed.flavor_keys or parsed.flavor_text):
        return products
    evidenced = [p for p in products if has_flavor_evidence(parsed, p.name, vocab)]
    return evidenced


_AROMA_PREFIX_RE = re.compile(r".*?с\s+ароматом\s+", re.IGNORECASE)
_PARENS_RE = re.compile(r"\([^)]*\)")
_WEIGHT_TAIL_RE = re.compile(r",?\s*\d+\s*[гgГG][рrРR]?[.,]?\s*$", re.IGNORECASE)


def _flavor_part(product_name: str) -> str:
    """Вкусовая часть названия: без бренда-префикса, скобок и граммовки."""
    part = product_name
    if "·" in part:
        part = part.split("·", 1)[1]
    m = _AROMA_PREFIX_RE.match(part)
    if m:
        part = part[m.end():]
    part = _PARENS_RE.sub("", part)
    part = _WEIGHT_TAIL_RE.sub("", part).strip()
    return part


def _component_count(text: str) -> int:
    """Сколько вкусовых компонентов перечислено (по запятым и союзу «и»)."""
    if not text:
        return 0
    commas = text.count(",")
    ands = len(re.findall(r"\bи\b", text, re.IGNORECASE))
    return commas + ands + 1


def _query_component_count(parsed: ParsedQuery, vocab: Vocabulary) -> int:
    """Сколько вкусов запросил пользователь (для отличия простого вкуса от микса)."""
    src = parsed.flavor_text
    if not src and parsed.flavor_keys:
        src = ", ".join(
            vocab.flavors[fk].display
            for fk in parsed.flavor_keys
            if fk in vocab.flavors
        )
    structural = _component_count(normalize_text(src)) if src else 0
    return max(structural, len(parsed.flavor_keys or []))


def filter_mix_overmatch(
    parsed: ParsedQuery,
    products: list[CatalogProduct],
    vocab: Vocabulary,
    *,
    mix_threshold: int = 3,
) -> list[CatalogProduct]:
    """Убрать миксы 3+ компонентов, если запрос — простой вкус.

    «черешневый сок» не должен матчиться на «вишня, меренга, персик» —
    одно общее слово цепляет чужой микс.
    """
    q_comps = _query_component_count(parsed, vocab)
    if q_comps >= mix_threshold:
        return products  # пользователь сам просит микс — не фильтруем
    return [
        p
        for p in products
        if _component_count(_flavor_part(p.name)) < mix_threshold
    ]


def _check_candidates(
    parsed: ParsedQuery,
    products: list[CatalogProduct],
    vocab: Vocabulary,
) -> list[CatalogProduct]:
    """Отсев кандидатов для проверки наличия по списку.

    1. Убираем не-табак (уголь, кальяны, аксессуары) при заданном вкусе.
    2. Оставляем только товары со следом искомого вкуса (анти-«только бренд»).
    3. Убираем чужие миксы 3+ компонентов для простого запроса.
    """
    if not (parsed.flavor_keys or parsed.flavor_text):
        return products
    no_accessory = [p for p in products if not NON_TOBACCO_NAME_RE.search(p.name)]
    evidenced = filter_by_flavor_evidence(parsed, no_accessory, vocab)
    return filter_mix_overmatch(parsed, evidenced, vocab)


def _live_max_searches() -> int:
    """Макс. поисковых запросов к сайту за одну позицию при live-проверке в пакете."""
    return int(os.environ.get("CATALOG_LIVE_MAX_SEARCHES", "3"))


def _batch_live_delay() -> float:
    """Задержка (сек) между позициями при live-проверке списка (cold cache)."""
    return float(os.environ.get("CATALOG_BATCH_DELAY", "0.3"))


def _js_object_to_dict(js_object: str) -> dict[str, Any]:
    """Конвертация аргумента JCCatalogItem({...}) в dict.

    Bitrix современных версий передаёт валидный JSON — пробуем его первым.
    Старый формат использует одинарные кавычки — запасной вариант.
    """
    try:
        return json.loads(js_object)
    except json.JSONDecodeError:
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

    def _gather_candidates(
        self,
        parsed: ParsedQuery,
        *,
        max_searches: int | None = None,
    ) -> list[CatalogProduct]:
        """Собрать кандидатов с сайта.

        max_searches — ограничение числа поисковых запросов (None = без ограничений).
        При max_searches != None раздел-fallback пропускается: экономим HTTP-запросы
        при пакетной live-проверке с холодным кэшем.
        """
        products: list[CatalogProduct] = []
        search_queries = self.vocab.build_search_terms(
            brand_key=parsed.brand_key,
            flavor_keys=parsed.flavor_keys or [],
            flavor_text=parsed.flavor_text,
            weight=parsed.weight_grams,
        )
        if not search_queries:
            search_queries = [parsed.raw]

        limit = max_searches if max_searches is not None else len(search_queries)
        for search_q in search_queries[:limit]:
            page = self.search(search_q)
            products.extend(page.products)

        # Fallback через раздел каталога — только в полном (не throttled) режиме
        if max_searches is None:
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

    def check_product(
        self,
        query: str,
        *,
        min_score: float = 0.48,
        _max_searches: int | None = None,
    ) -> ProductCheckResult:
        """Проверить одну позицию: разбор запроса + поиск + наличие.

        _max_searches — внутренний параметр для ограничения HTTP-запросов
        при пакетной live-проверке (передаётся из check_products).
        """
        from oshisha import catalog_cache

        if catalog_cache.is_ready("oshisha"):
            snap_result = catalog_cache.check_product_using_snapshot(
                self, query, site_id="oshisha", min_score=min_score
            )
            if snap_result is not None:
                return snap_result

        parsed = parse_query(query, self.vocab)
        products = self._gather_candidates(parsed, max_searches=_max_searches)
        products = _check_candidates(parsed, products, self.vocab)
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
        """Проверить список названий из бота.

        При пакетном запросе с холодным кэшем автоматически включает throttle:
        - ограничивает число HTTP-запросов на позицию (CATALOG_LIVE_MAX_SEARCHES, по умолч. 3)
        - добавляет задержку между позициями (CATALOG_BATCH_DELAY, по умолч. 0.3 с)
        """
        from oshisha import catalog_cache

        lines = [n.strip() for n in names if n.strip() and not n.strip().startswith("#")]

        is_batch = len(lines) > 3
        cache_cold = not catalog_cache.is_ready("oshisha")
        throttle = is_batch and cache_cold
        max_searches: int | None = _live_max_searches() if throttle else None
        delay = _batch_live_delay() if throttle else 0.0

        results: list[ProductCheckResult] = []
        for i, line in enumerate(lines):
            if throttle and i > 0:
                time.sleep(delay)
            results.append(self.check_product(line, min_score=min_score, _max_searches=max_searches))
        return results
