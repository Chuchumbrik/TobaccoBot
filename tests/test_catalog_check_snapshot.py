from oshisha.catalog import CatalogProduct
from oshisha import catalog_cache


def _product(pid: str, name: str, *, can_buy: bool = True) -> CatalogProduct:
    return CatalogProduct(
        id=pid,
        name=name,
        url=f"/p/{pid}/",
        can_buy=can_buy,
        max_quantity=10 if can_buy else 0,
        price=100.0,
        base_price=None,
        currency="RUB",
    )


class _FakeVocab:
    brands = {}
    flavors = {}


class _FakeCatalog:
    vocab = _FakeVocab()


def test_check_product_using_snapshot_finds_match(monkeypatch):
    snap_products = [
        _product("1", "BlackBurn Darkside малина 200г"),
        _product("2", "Musthave Кола 100г"),
    ]
    from oshisha.catalog_snapshot import CatalogSnapshot
    import time

    snap = CatalogSnapshot(
        products=snap_products, built_at=time.time(), site_id="oshisha"
    )

    monkeypatch.setattr(catalog_cache, "get_snapshot", lambda site_id=None: snap)
    monkeypatch.setattr(catalog_cache, "is_ready", lambda site_id=None: True)
    monkeypatch.setattr(catalog_cache, "_live_fallback_min_score", lambda: 0.3)

    from oshisha.query_parser import ParsedQuery
    from oshisha.vocabulary import get_vocabulary

    real_vocab = get_vocabulary()
    _FakeCatalog.vocab = real_vocab

    result = catalog_cache.check_product_using_snapshot(
        _FakeCatalog(),  # type: ignore[arg-type]
        "малина",
        min_score=0.35,
    )
    assert result is not None
    assert result.status in ("есть", "нет")
    assert result.matched_name and "малин" in result.matched_name.lower()


def test_check_product_using_snapshot_returns_none_when_weak(monkeypatch):
    from oshisha.catalog_snapshot import CatalogSnapshot
    import time

    snap = CatalogSnapshot(
        products=[_product("9", "Совсем другой вкус 250г")],
        built_at=time.time(),
        site_id="oshisha",
    )
    monkeypatch.setattr(catalog_cache, "get_snapshot", lambda site_id=None: snap)
    monkeypatch.setattr(catalog_cache, "is_ready", lambda site_id=None: True)

    from oshisha.vocabulary import get_vocabulary

    _FakeCatalog.vocab = get_vocabulary()
    result = catalog_cache.check_product_using_snapshot(
        _FakeCatalog(),  # type: ignore[arg-type]
        "малина",
        min_score=0.48,
    )
    assert result is None
