import time

from oshisha import catalog_cache
from oshisha.catalog import CatalogProduct
from oshisha.catalog_snapshot import CatalogSnapshot


def _product(pid: str, name: str) -> CatalogProduct:
    return CatalogProduct(
        id=pid,
        name=name,
        url=f"/p/{pid}/",
        can_buy=True,
        max_quantity=10,
        price=100.0,
        base_price=None,
        currency="RUB",
    )


def test_is_ready_false_while_updating(monkeypatch):
    snap = CatalogSnapshot(
        products=[_product("1", "Test 200г")],
        built_at=time.time(),
        site_id="stub",
    )
    catalog_cache.invalidate()
    catalog_cache.refresh_site("stub", lambda: snap, force=True)
    assert catalog_cache.is_ready("stub")

    catalog_cache.set_updating(True)
    try:
        assert not catalog_cache.is_ready("stub")
        html = catalog_cache.stock_disclaimer_html("stub")
        assert "обновляется" in html.lower()
    finally:
        catalog_cache.set_updating(False)
    assert catalog_cache.is_ready("stub")


def test_refresh_site_per_site_id(monkeypatch):
    catalog_cache.invalidate()
    s1 = CatalogSnapshot(
        products=[_product("a", "Site A малина")],
        built_at=time.time(),
        site_id="stub",
    )
    s2 = CatalogSnapshot(
        products=[_product("b", "Site B клубника")],
        built_at=time.time(),
        site_id="oshisha",
    )
    catalog_cache.refresh_site("stub", lambda: s1, force=True)
    catalog_cache.refresh_site("oshisha", lambda: s2, force=True)
    assert catalog_cache.get_snapshot("stub") is not None
    assert catalog_cache.get_snapshot("oshisha") is not None
    assert catalog_cache.get_snapshot("stub").product_count == 1
