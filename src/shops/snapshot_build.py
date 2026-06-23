"""Сборка снимка каталога для каждого провайдера."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from oshisha.catalog import CatalogProduct
from oshisha.catalog_snapshot import CatalogSnapshot, build_full_snapshot

if TYPE_CHECKING:
    from shops.protocol import ShopProvider

logger = logging.getLogger(__name__)


def _stub_products(provider: ShopProvider) -> list[CatalogProduct]:
    """Демо-каталог для stub (сравнение и тесты без HTTP)."""
    name = provider.info.display_name
    base = provider.info.base_url
    rows = [
        ("s1", f"[{name}] BlackBurn малина 200г", True, 10, 450.0),
        ("s2", f"[{name}] Darkside Грейпфрут 100г", True, 5, 380.0),
        ("s3", f"[{name}] Musthave Клубника 125г", True, 8, 520.0),
        ("s4", f"[{name}] Daily Hookah Арбуз 250г", False, 0, 400.0),
        ("s5", f"[{name}] Sebero Персик 200г", True, 3, 410.0),
        ("s6", f"[{name}] Bonche Виноград 60г", True, 12, 290.0),
    ]
    return [
        CatalogProduct(
            id=pid,
            name=title,
            url=base,
            can_buy=can_buy,
            max_quantity=qty,
            price=price,
            base_price=price,
            currency="RUB",
        )
        for pid, title, can_buy, qty, price in rows
    ]


def build_snapshot_for_provider(provider: ShopProvider) -> CatalogSnapshot:
    """Полный снимок для одного сайта."""
    site_id = provider.info.site_id
    inner = getattr(provider, "inner", None)
    if inner is not None and hasattr(inner, "_ensure_login"):
        catalog = inner._ensure_login()
        snap = build_full_snapshot(catalog)
        snap.site_id = site_id
        return snap

    products = _stub_products(provider)
    logger.info("catalog snapshot %s: %d stub products", site_id, len(products))
    return CatalogSnapshot(
        site_id=site_id,
        products=products,
        built_at=time.time(),
        sections_scanned=1,
        fetch_errors=0,
    )
