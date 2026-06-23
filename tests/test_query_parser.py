"""Тесты разбора строк запроса."""

from __future__ import annotations

from oshisha.query_parser import parse_query
from oshisha.vocabulary import Vocabulary, get_vocabulary


def test_parse_brand_flavor_weight():
    vocab = get_vocabulary()
    p = parse_query("Бб - черешневый сок 200гр", vocab)
    assert p.brand_key == "blackburn"
    assert p.weight_grams == 200
    assert p.flavor_keys or p.flavor_text


def test_parse_pack_suffix():
    vocab = get_vocabulary()
    p = parse_query("Арбуз 200 3х", vocab)
    assert p.weight_grams == 200
    assert p.pack_count == 3


def test_parse_trailing_weight():
    vocab = get_vocabulary()
    p = parse_query("малина 200", vocab)
    assert p.weight_grams == 200
