"""Полный снимок каталога и локальный поиск по вкусу."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from .catalog import (
    CatalogProduct,
    _dedupe_products,
    _weight_in_name,
    is_in_stock,
    score_name_match,
    score_product_match,
)
from .flavor_search import (
    FlavorSearchHit,
    FlavorSearchResult,
    _brand_key_from_product,
    _flavor_only_parsed,
    _flavor_rank,
)
from .query_parser import ParsedQuery
from .vocabulary import Vocabulary, normalize_text

logger = logging.getLogger(__name__)

# Разделы каталога, исключаемые из снимка.
# Сигареты, снюс, трубочный табак, электронки и жидкости —
# они не относятся к кальянному поиску и засоряют результаты.
_EXCLUDED_SECTIONS: frozenset[str] = frozenset({
    # ── Электронные сигареты / жидкости / картриджи ───────────────────────────
    "/catalog/angry_vape_salt_10_ml_2_mg_/",
    "/catalog/brusko_zhidkosti/",
    "/catalog/hqd_rave/",
    "/catalog/izi_1/",
    "/catalog/kartridzhi_2/",
    "/catalog/mnogorazovye_elektronki_1/",
    "/catalog/monsterwapor_salt_10_ml_2_mg_/",
    "/catalog/odnorazovye_elektronki_1/",
    "/catalog/plonq_3/",
    "/catalog/skala_salt_10_ml_2_mg_/",
    "/catalog/soak_3/",
    "/catalog/stiki_1/",               # IQOS / HnB стики
    "/catalog/ustroystvo_nagrevaniya_tabaka/",  # устройства нагревания
    # ── Сигареты и сигары ─────────────────────────────────────────────────────
    "/catalog/barclay/",
    "/catalog/corsar_of_the_queen_10sht/",
    "/catalog/corsar_of_the_queen_20sht/",
    "/catalog/corsar_of_the_queen_2sht/",
    "/catalog/corsar_of_the_queen_35gr/",
    "/catalog/dakota/",
    "/catalog/dakota_2sht/",
    "/catalog/dimitrino/",
    "/catalog/handelsgold/",
    "/catalog/k_ritter_1/",
    "/catalog/peter_ralf/",
    # ── Снюс и жевательный табак ──────────────────────────────────────────────
    "/catalog/angry_chew/",
    "/catalog/monster_chewer/",        # Monster Chewer — жевательный табак
    "/catalog/odens_1/",
    "/catalog/siberia_1/",             # снюс (siberia без _1 — кальянный)
    "/catalog/stimoral/",
    # ── Трубочный табак и докха ───────────────────────────────────────────────
    "/catalog/mac_baren/",             # Mac Baren — трубочный/сигаретный табак
    "/catalog/mac_baren_1/",
    "/catalog/pepe/",                  # Pepe — трубочный табак
    "/catalog/pepe_1/",
    "/catalog/sebero_dokha/",          # SEBERO Докха — мидвах, не кальян
    "/catalog/trubki_ch/",
    "/catalog/trubki_est_chye_/",
    "/catalog/trubochnyy_tabak_iz_pogara/",
    "/catalog/uolter_reyli/",
    "/catalog/walter_raleigh/",
    # ── Сигариллы ─────────────────────────────────────────────────────────────
    "/catalog/cherokee/",              # Cherokee — сигариллы
    "/catalog/cherokee_1/",
    "/catalog/cherokee_2/",
    "/catalog/cherokee_premium/",
    # ── Травяной/альтернативный табак ─────────────────────────────────────────
    "/catalog/trava/",                 # Трава — не кальянный табак
    # ── Курительный и жевательный (не кальянный) ──────────────────────────────
    "/catalog/arq/",                   # ARQ Tobacco — жевательный табак
    "/catalog/arq_1/",
    "/catalog/harvest/",               # Harvest — курительный трубочный
    "/catalog/kharvest/",
    "/catalog/stanley/",               # Stanley — курительный табак
})


@dataclass
class CatalogSnapshot:
    products: list[CatalogProduct]
    built_at: float
    site_id: str = "oshisha"
    sections_scanned: int = 0
    fetch_errors: int = 0

    @property
    def product_count(self) -> int:
        return len(self.products)

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.built_at)


def _section_delay() -> float:
    return float(os.environ.get("CATALOG_FETCH_DELAY", "0.15"))


def build_full_snapshot(catalog: object) -> CatalogSnapshot:
    """
    Обходит все разделы брендов из vocabulary и собирает товары.
    catalog: OshishaCatalog
    """
    vocab: Vocabulary = catalog.vocab  # type: ignore[attr-defined]
    sections = sorted(
        {
            b.catalog_section
            for b in vocab.brands.values()
            if b.catalog_section and b.catalog_section not in _EXCLUDED_SECTIONS
        }
    )
    logger.info(
        "catalog snapshot: will scan %d sections (%d excluded)",
        len(sections),
        len(_EXCLUDED_SECTIONS),
    )
    all_products: list[CatalogProduct] = []
    errors = 0
    delay = _section_delay()

    for i, section in enumerate(sections):
        try:
            if i > 0 and delay > 0:
                time.sleep(delay)
            chunk = catalog.fetch_section_all_pages(section)  # type: ignore[attr-defined]
            all_products.extend(chunk)
            logger.info(
                "catalog snapshot: %s → %d items (total raw %d)",
                section,
                len(chunk),
                len(all_products),
            )
        except Exception as exc:
            errors += 1
            logger.warning("catalog snapshot section %r failed: %s", section, exc)

    products = _dedupe_products(all_products)
    logger.info(
        "catalog snapshot ready: %d products, %d sections, %d errors",
        len(products),
        len(sections),
        errors,
    )
    return CatalogSnapshot(
        products=products,
        built_at=time.time(),
        site_id="oshisha",
        sections_scanned=len(sections),
        fetch_errors=errors,
    )


def _query_tokens(parsed: ParsedQuery, query: str, vocab: Vocabulary) -> set[str]:
    tokens: set[str] = set()
    blob = parsed.flavor_text or query
    tokens.update(t for t in normalize_text(blob).split() if len(t) >= 2)
    for fk in parsed.flavor_keys or []:
        flavor = vocab.flavors.get(fk)
        if flavor:
            tokens.update(
                t for t in normalize_text(flavor.display).split() if len(t) >= 2
            )
            for term in flavor.site_terms[:6]:
                tokens.update(
                    t for t in normalize_text(term).split() if len(t) >= 2
                )
    if parsed.brand_key and parsed.brand_key in vocab.brands:
        brand = vocab.brands[parsed.brand_key]
        for sn in brand.site_names[:3]:
            tokens.update(t for t in normalize_text(sn).split() if len(t) >= 2)
    return tokens


def _prefilter_products(
    products: list[CatalogProduct],
    parsed: ParsedQuery,
    query: str,
    vocab: Vocabulary,
    *,
    max_candidates: int = 1200,
) -> list[CatalogProduct]:
    tokens = _query_tokens(parsed, query, vocab)
    if not tokens:
        return products[:max_candidates]

    matched: list[CatalogProduct] = []
    for product in products:
        pn = normalize_text(product.name)
        if any(t in pn for t in tokens):
            matched.append(product)

    if len(matched) < 30:
        return products[:max_candidates]
    if len(matched) > max_candidates:
        return matched[:max_candidates]
    return matched


def search_by_flavor_in_snapshot(
    products: list[CatalogProduct],
    query: str,
    vocab: Vocabulary,
    *,
    limit: int = 15,
    min_score: float = 0.42,
    in_stock_only: bool = False,
) -> FlavorSearchResult:
    """Поиск по вкусу в готовом снимке (без HTTP)."""
    parsed = _flavor_only_parsed(query, vocab)

    if not parsed.flavor_keys and parsed.flavor_text:
        parsed.flavor_keys = vocab.match_flavors(
            parsed.flavor_text, brand_key=parsed.brand_key
        )

    flavor_keys = parsed.flavor_keys or []
    candidates = _prefilter_products(products, parsed, query, vocab)

    if parsed.brand_key:
        brand = vocab.brands.get(parsed.brand_key)
        if brand:
            bnorm = {normalize_text(sn) for sn in brand.site_names}
            brand_filtered = [
                p
                for p in candidates
                if any(bn in normalize_text(p.name) for bn in bnorm)
            ]
            if brand_filtered:
                candidates = brand_filtered

    scored: list[tuple[float, CatalogProduct, str | None, str | None]] = []
    for product in candidates:
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
        rank = _flavor_rank(product.name, flavor_keys, vocab)
        hits.append(
            FlavorSearchHit(
                flavor_query=query,
                product=product,
                brand_key=bk,
                brand_display=bd,
                status="есть" if in_stock else "нет",
                match_score=round(score, 3),
                requested_weight_g=parsed.weight_grams,
                matched_weight_g=matched_w,
                flavor_rank=rank,
            )
        )
        if len(hits) >= limit:
            break

    hits.sort(key=lambda h: (h.status != "есть", h.flavor_rank, -h.match_score))
    return FlavorSearchResult(
        query=query,
        parsed=parsed,
        hits=hits,
        flavor_keys_matched=flavor_keys,
    )
