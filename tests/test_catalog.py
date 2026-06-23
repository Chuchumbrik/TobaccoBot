"""Тесты парсинга каталога и scoring."""

from __future__ import annotations

from pathlib import Path

from oshisha.catalog import (
    is_in_stock,
    parse_catalog_html,
    score_name_match,
    score_product_match,
)
from oshisha.query_parser import parse_query
from oshisha.vocabulary import get_vocabulary

FIXTURE = Path(__file__).parent / "fixtures" / "catalog_jccatalog.html"


def test_parse_catalog_html_jccatalog():
    html = FIXTURE.read_text(encoding="utf-8")
    page = parse_catalog_html(html, page_url="https://oshisha.cc/catalog/test/")
    assert len(page.products) == 2
    assert page.products[0].id == "1001"
    assert "200" in page.products[0].name
    assert page.products[0].can_buy is True
    assert page.products[1].can_buy is False


def test_score_name_match_substring():
    assert score_name_match("малина", "BlackBurn Малина 200гр") >= 0.45


def test_is_in_stock():
    html = FIXTURE.read_text(encoding="utf-8")
    page = parse_catalog_html(html)
    assert is_in_stock(page.products[0]) is True
    assert is_in_stock(page.products[1]) is False


def test_score_product_match_with_parsed():
    vocab = get_vocabulary()
    parsed = parse_query("малина 200", vocab)
    html = FIXTURE.read_text(encoding="utf-8")
    product = parse_catalog_html(html).products[0]
    score = score_product_match(parsed, product, vocab)
    assert 0 <= score <= 1.0


def _make_product(name: str) -> "CatalogProduct":
    from oshisha.catalog import CatalogProduct
    return CatalogProduct(
        id="test", name=name, url="", can_buy=True,
        max_quantity=None, price=None, base_price=None, currency=None,
    )


class TestColorConflictPenalty:
    """Регрессия: «красная смородина» не должна матчить «Чёрная смородина»."""

    def test_krasnaya_smorodina_does_not_match_chornaya(self):
        vocab = get_vocabulary()
        parsed = parse_query("красная смородина", vocab)
        product = _make_product("Bonche с ароматом Чёрная смородина (Black Currant), 60гр.")
        score = score_product_match(parsed, product, vocab)
        assert score < 0.48, f"ожидали score<0.48, получили {score:.3f}"

    def test_krasnaya_smorodina_matches_krasnaya(self):
        vocab = get_vocabulary()
        parsed = parse_query("красная смородина", vocab)
        product = _make_product("Bonche с ароматом Красная смородина (Red Currant), 60гр.")
        score = score_product_match(parsed, product, vocab)
        assert score >= 0.48, f"ожидали score>=0.48, получили {score:.3f}"

    def test_chornaya_smorodina_matches_chornaya(self):
        vocab = get_vocabulary()
        parsed = parse_query("черная смородина", vocab)
        product = _make_product("Bonche с ароматом Чёрная смородина (Black Currant), 60гр.")
        score = score_product_match(parsed, product, vocab)
        assert score >= 0.48, f"ожидали score>=0.48, получили {score:.3f}"

    def test_chornaya_does_not_match_krasnaya(self):
        vocab = get_vocabulary()
        parsed = parse_query("черная смородина", vocab)
        product = _make_product("Bonche с ароматом Красная смородина (Red Currant), 60гр.")
        score = score_product_match(parsed, product, vocab)
        assert score < 0.48, f"ожидали score<0.48, получили {score:.3f}"


class TestBrandPenaltyUnknownBrands:
    """Регрессия: «Догма Персик» не должна матчить Trofimoff's."""

    def test_dogma_peach_does_not_match_trofimoff(self):
        vocab = get_vocabulary()
        parsed = parse_query("Догма Персик", vocab)
        product = _make_product('Trofimoff"s Burley с ароматом Персик(Peche), 25 гр.')
        score = score_product_match(parsed, product, vocab)
        assert score < 0.48, f"ожидали score<0.48, получили {score:.3f}"

    def test_darkside_pomelo_does_not_match_chabacco(self):
        vocab = get_vocabulary()
        parsed = parse_query("Дарк сайд Помело", vocab)
        product = _make_product("Chabacco Medium с ароматом Помело (Pomelo), 40гр.")
        score = score_product_match(parsed, product, vocab)
        assert score < 0.48, f"ожидали score<0.48, получили {score:.3f}"

    def test_dogma_brand_recognized(self):
        vocab = get_vocabulary()
        parsed = parse_query("Догма Персик", vocab)
        assert parsed.brand_key == "dogma", f"ожидали brand_key='dogma', получили {parsed.brand_key!r}"

    def test_darkside_brand_recognized(self):
        vocab = get_vocabulary()
        parsed = parse_query("Дарк сайд Помело", vocab)
        assert parsed.brand_key == "darkside", f"ожидали brand_key='darkside', получили {parsed.brand_key!r}"
