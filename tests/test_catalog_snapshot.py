from oshisha.catalog import CatalogProduct
from oshisha.catalog_snapshot import search_by_flavor_in_snapshot
from oshisha.vocabulary import get_vocabulary


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


def test_search_in_snapshot_finds_raspberry():
    vocab = get_vocabulary()
    products = [
        _product("1", "BlackBurn Шоколад малина 200г"),
        _product("2", "Darkside Кола 100г"),
        _product("3", "Musthave Клубника 125г"),
    ]
    result = search_by_flavor_in_snapshot(products, "малина", vocab, limit=5)
    assert result.hits
    assert any("малин" in h.product.name.lower() for h in result.hits)
