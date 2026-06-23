"""Гейт по вкусу в проверке: анти-«только бренд» и отсев не-табака."""

from oshisha.catalog import (
    CatalogProduct,
    NON_TOBACCO_NAME_RE,
    _check_candidates,
    has_flavor_evidence,
)
from oshisha.query_parser import parse_query
from oshisha.vocabulary import get_vocabulary


def _p(name: str) -> CatalogProduct:
    return CatalogProduct(
        id=name, name=name, url="/p/", can_buy=True,
        max_quantity=10, price=100.0, base_price=None, currency="RUB",
    )


def test_non_tobacco_regex_matches_charcoal():
    assert NON_TOBACCO_NAME_RE.search("Уголь BlackBurn кокосовый 12 шт (25 мм)")
    assert not NON_TOBACCO_NAME_RE.search("BlackBurn Сливочная кукуруза, 200гр.")


def test_charcoal_filtered_out_for_flavor_query():
    vocab = get_vocabulary()
    parsed = parse_query("Бб - пудинг", vocab)
    products = [
        _p("Уголь BlackBurn кокосовый 12 шт (25 мм)"),
        _p("BlackBurn Сливочная кукуруза, 200гр."),
    ]
    out = _check_candidates(parsed, products, vocab)
    names = [p.name for p in out]
    assert all("Уголь" not in n for n in names)


def test_brand_only_mismatch_yields_no_candidates():
    """Deus love is: нужного вкуса нет → ни один Deus не должен пройти как «есть»."""
    vocab = get_vocabulary()
    parsed = parse_query("Deus - love is 200гр", vocab)
    products = [
        _p("Deus Perfume с ароматом Ласт сизон (Last Season), 200гр."),
        _p("Deus Perfume с ароматом Ганимед (Ganymede), 200гр."),
    ]
    out = _check_candidates(parsed, products, vocab)
    assert out == []


def test_matching_flavor_survives_gate():
    vocab = get_vocabulary()
    parsed = parse_query("Сарма малина 200", vocab)
    good = _p("САРМА с ароматом Лесная Малина, 200 гр.")
    bad = _p("САРМА 360 Крепкая с ароматом Земляника, 200 гр.")
    out = _check_candidates(parsed, [good, bad], vocab)
    assert good in out
    assert bad not in out


def test_no_flavor_requested_keeps_everything():
    vocab = get_vocabulary()
    parsed = parse_query("Сарма 200", vocab)
    products = [_p("САРМА 360 Крепкая с ароматом Вишня, 200 гр.")]
    assert _check_candidates(parsed, products, vocab) == products


def test_simple_query_rejects_3component_mix():
    """«черешневый сок» не должен матчиться на чужой микс из 3 компонентов."""
    vocab = get_vocabulary()
    parsed = parse_query("Бб - черешневый сок 200гр", vocab)
    mix = _p("BlackBurn HiT с ароматом вишня, меренга, персик (Cherry Crime), 30гр.")
    out = _check_candidates(parsed, [mix], vocab)
    assert mix not in out


def test_simple_query_keeps_single_flavor():
    vocab = get_vocabulary()
    parsed = parse_query("Бб - черешневый сок 200гр", vocab)
    exact = _p("BlackBurn Cherry Garden (Черешневый Сок), 200 гр.")
    out = _check_candidates(parsed, [exact], vocab)
    assert exact in out
