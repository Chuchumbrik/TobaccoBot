"""Тесты мульти-сайтового слоя и сравнения."""

from __future__ import annotations

from shops.compare import compare_list, compare_search, summarize_search_prices
from shops.format_compare import format_compare_search
from shops.hub import ShopHub
from shops.providers.stub import StubShopProvider
from shops.registry import registered_site_ids


def test_registry_has_oshisha():
    assert "oshisha" in registered_site_ids()


def test_compare_search_two_stubs():
    hub = ShopHub(providers=[StubShopProvider(site_id="a"), StubShopProvider(site_id="b")])
    compare = hub.compare_search_flavor("малина", limit=3)
    assert len(compare.sites) == 2
    assert all(s.error is None for s in compare.sites)
    assert all(s.result and s.result.hits for s in compare.sites)
    summary = summarize_search_prices(compare)
    assert len(summary.rows) == 2
    text = format_compare_search(compare)
    assert "малина" in text.lower() or "Сравнение" in text


def test_compare_list_mixed():
    providers = [StubShopProvider(site_id="stub")]
    compare = compare_list(providers, ["Бб малина 200", "вишня 200"])
    assert len(compare.lines) == 2
    assert compare.lines[0].by_site["stub"].status == "есть"


def test_primary_delegates_to_first_provider():
    hub = ShopHub(providers=[StubShopProvider(site_id="first"), StubShopProvider(site_id="second")])
    assert hub.primary_site_id == "first"
    results = hub.check_list(["test line"])
    assert results[0].matched_name.startswith("[Stub Shop]")
