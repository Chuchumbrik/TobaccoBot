"""Кэш снимков каталога по site_id (прогрев при старте бота)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .catalog import (
    CatalogProduct,
    ProductCheckResult,
    find_best_match,
    is_in_stock,
    parse_query,
    _check_candidates,
    _weight_in_name,
)
from .catalog_snapshot import (
    CatalogSnapshot,
    search_by_flavor_in_snapshot,
)
from .flavor_search import FlavorSearchResult
from .query_parser import ParsedQuery
from .vocabulary import Vocabulary, get_vocabulary, normalize_text

if TYPE_CHECKING:
    from .catalog import OshishaCatalog

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / "data" / "cache"

_lock = threading.Lock()
_snapshots: dict[str, CatalogSnapshot] = {}
_updating = False
_refreshing_sites: set[str] = set()


def _live_fallback_min_score() -> float:
    return float(os.environ.get("CATALOG_CHECK_LIVE_FALLBACK_SCORE", "0.65"))


def _ttl_seconds() -> float:
    return float(os.environ.get("CATALOG_CACHE_TTL", "3600"))


def _primary_site_id() -> str:
    raw = os.environ.get("SHOP_SITES", "oshisha")
    return raw.split(",")[0].strip().lower() or "oshisha"


def is_warmup_enabled() -> bool:
    return os.environ.get("CATALOG_WARMUP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def is_updating() -> bool:
    with _lock:
        return _updating


def set_updating(value: bool) -> None:
    global _updating
    with _lock:
        _updating = value


def is_ready(site_id: str | None = None) -> bool:
    sid = site_id or _primary_site_id()
    with _lock:
        if _updating:
            return False
        snap = _snapshots.get(sid)
        if snap is None:
            return False
        return snap.age_seconds < _ttl_seconds()


def is_primary_ready() -> bool:
    return is_ready(_primary_site_id())


def get_snapshot(site_id: str | None = None) -> CatalogSnapshot | None:
    sid = site_id or _primary_site_id()
    with _lock:
        snap = _snapshots.get(sid)
        if snap is None:
            return None
        if snap.age_seconds >= _ttl_seconds():
            return None
        return snap


def status_line() -> str:
    if is_updating():
        return "кэш каталога: обновление…"
    parts: list[str] = []
    with _lock:
        for sid, snap in sorted(_snapshots.items()):
            if snap.age_seconds < _ttl_seconds():
                parts.append(f"{sid}={snap.product_count}")
    if not parts:
        return "кэш каталога: не готов"
    return "кэш каталога: " + ", ".join(parts)


def stock_disclaimer_html(site_id: str | None = None) -> str:
    if is_updating():
        return (
            "<i>Каталог обновляется — поиск и наличие временно по live-данным сайта. "
            "При добавлении в корзину наличие проверяется снова.</i>\n"
        )
    snap = get_snapshot(site_id)
    if snap is None:
        return ""
    age_min = max(1, int(snap.age_seconds / 60))
    return (
        f"<i>Наличие по снимку каталога (~{age_min} мин назад). "
        f"При добавлении в корзину проверяется снова.</i>\n"
    )


def _cache_path(site_id: str) -> Path:
    return CACHE_DIR / f"catalog_snapshot_{site_id}.json"


def _save_disk(snap: CatalogSnapshot) -> None:
    if os.environ.get("CATALOG_DISK_SAVE", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "site_id": snap.site_id,
            "built_at": snap.built_at,
            "sections_scanned": snap.sections_scanned,
            "fetch_errors": snap.fetch_errors,
            "products": [
                {
                    "id": p.id,
                    "name": p.name,
                    "url": p.url,
                    "can_buy": p.can_buy,
                    "max_quantity": p.max_quantity,
                    "price": p.price,
                    "base_price": p.base_price,
                    "currency": p.currency,
                }
                for p in snap.products
            ],
        }
        _cache_path(snap.site_id).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("catalog disk save failed %s: %s", snap.site_id, exc)


def refresh_site(
    site_id: str,
    builder: Callable[[], CatalogSnapshot],
    *,
    force: bool = False,
) -> CatalogSnapshot:
    """Собирает снимок одного сайта (блокирующий вызов)."""
    with _lock:
        if not force:
            existing = _snapshots.get(site_id)
            if existing is not None and existing.age_seconds < _ttl_seconds():
                return existing
        if site_id in _refreshing_sites and site_id in _snapshots:
            return _snapshots[site_id]

    with _lock:
        _refreshing_sites.add(site_id)
    try:
        snap = builder()
        snap.site_id = site_id
        with _lock:
            _snapshots[site_id] = snap
        _save_disk(snap)
        return snap
    finally:
        with _lock:
            _refreshing_sites.discard(site_id)


def invalidate(site_id: str | None = None) -> None:
    with _lock:
        if site_id is None:
            _snapshots.clear()
        else:
            _snapshots.pop(site_id, None)


def _filter_candidates_for_parsed(
    products: list[CatalogProduct],
    parsed: ParsedQuery,
    query: str,
    vocab: Vocabulary,
) -> list[CatalogProduct]:
    from .catalog_snapshot import _prefilter_products

    candidates = _prefilter_products(products, parsed, query, vocab)
    if parsed.brand_key:
        brand = vocab.brands.get(parsed.brand_key)
        if brand:
            bnorm = {normalize_text(sn) for sn in brand.site_names}
            brand_filtered = [
                p for p in candidates if any(bn in normalize_text(p.name) for bn in bnorm)
            ]
            if brand_filtered:
                candidates = brand_filtered
    return candidates


def _product_check_result(
    query: str,
    parsed: ParsedQuery,
    product: CatalogProduct,
    score: float,
) -> ProductCheckResult:
    parsed_info = {
        "brand": parsed.brand_display,
        "flavors": parsed.flavor_keys,
        "weight_g": parsed.weight_grams,
        "pack_count": parsed.pack_count,
        "summary": parsed.summary(),
    }
    in_stock = is_in_stock(product)
    matched_w = _weight_in_name(product.name)
    req_w = parsed.weight_grams
    status = "есть" if in_stock else "нет"
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


def check_product_snapshot(
    site_id: str,
    query: str,
    vocab: Vocabulary,
    *,
    min_score: float = 0.48,
) -> ProductCheckResult | None:
    snap = get_snapshot(site_id)
    if snap is None or not snap.products:
        return None
    parsed = parse_query(query, vocab)
    candidates = _filter_candidates_for_parsed(snap.products, parsed, query, vocab)
    candidates = _check_candidates(parsed, candidates, vocab)
    product, score = find_best_match(
        query, candidates, min_score=min_score, parsed=parsed, vocab=vocab
    )
    if product is None or score < _live_fallback_min_score():
        return None
    return _product_check_result(query, parsed, product, score)


def search_flavor_cached(
    site_id: str,
    query: str,
    vocab: Vocabulary,
    live_search: Callable[[], FlavorSearchResult],
    *,
    limit: int = 50,
    in_stock_only: bool = False,
) -> FlavorSearchResult:
    snap = get_snapshot(site_id)
    if snap is not None and snap.products:
        return search_by_flavor_in_snapshot(
            snap.products, query, vocab, limit=limit, in_stock_only=in_stock_only
        )
    return live_search()


def search_flavor(
    catalog: OshishaCatalog,
    query: str,
    *,
    site_id: str = "oshisha",
    limit: int = 50,
    in_stock_only: bool = False,
) -> FlavorSearchResult:
    from .flavor_search import search_by_flavor

    vocab = catalog.vocab
    return search_flavor_cached(
        site_id,
        query,
        vocab,
        lambda: search_by_flavor(
            catalog, query, limit=limit, in_stock_only=in_stock_only
        ),
        limit=limit,
        in_stock_only=in_stock_only,
    )


def check_product_using_snapshot(
    catalog: OshishaCatalog,
    query: str,
    *,
    site_id: str = "oshisha",
    min_score: float = 0.48,
) -> ProductCheckResult | None:
    return check_product_snapshot(site_id, query, catalog.vocab, min_score=min_score)


def product_from_snapshot(product_id: str, site_id: str | None = None) -> CatalogProduct | None:
    snap = get_snapshot(site_id)
    if snap is None:
        return None
    for p in snap.products:
        if p.id == product_id:
            return p
    return None


def verify_product_for_cart(
    catalog: OshishaCatalog,
    product: CatalogProduct,
    *,
    site_id: str = "oshisha",
) -> tuple[CatalogProduct, bool]:
    if site_id != "oshisha" and not hasattr(catalog, "search"):
        snap = get_snapshot(site_id)
        if snap:
            for p in snap.products:
                if p.id == product.id:
                    return p, is_in_stock(p)
        return product, is_in_stock(product)

    try:
        page = catalog.search(product.name[:120])
        for p in page.products:
            if p.id == product.id:
                return p, is_in_stock(p)
    except Exception as exc:
        logger.warning("verify_product_for_cart %r: %s", product.name, exc)

    fresh = product_from_snapshot(product.id, site_id)
    if fresh is not None:
        return fresh, is_in_stock(fresh)
    return product, is_in_stock(product)


# Совместимость: старый API
def refresh(catalog: OshishaCatalog, *, force: bool = False) -> CatalogSnapshot:
    from .catalog_snapshot import build_full_snapshot

    return refresh_site(
        "oshisha",
        lambda: build_full_snapshot(catalog),
        force=force,
    )
