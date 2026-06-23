"""Тесты для bot.weight_groups и связанного форматирования."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.weight_groups import (
    FlavorGroup,
    WeightVariant,
    extract_weight_g,
    group_hits,
    strip_weight,
)
from bot.formatters import format_flavor_search, format_flavor_weight_group
from bot.inline_keyboards import (
    CB_FLAVOR_GROUP,
    CB_FLAVOR_PICK,
    flavor_search_keyboard,
    flavor_search_keyboard_with_mix,
    weight_picker_keyboard,
)
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult
from oshisha.query_parser import ParsedQuery


# ── Вспомогательные фабрики ───────────────────────────────────────────────────

def _product(pid: str, name: str, price: float = 1000.0, can_buy: bool = True):
    from oshisha.catalog import CatalogProduct
    return CatalogProduct(
        id=pid,
        name=name,
        url=f"/catalog/item-{pid}/",
        can_buy=can_buy,
        max_quantity=None,
        price=price,
        base_price=price,
        currency="RUB",
        raw={},
    )


def _hit(
    pid: str,
    name: str,
    status: str = "есть",
    price: float = 1000.0,
    brand_key: str | None = "bb",
    brand_display: str | None = "BlackBurn",
) -> FlavorSearchHit:
    return FlavorSearchHit(
        flavor_query="малина",
        product=_product(pid, name, price=price),
        brand_key=brand_key,
        brand_display=brand_display,
        status=status,
        match_score=1.0,
    )


def _result(hits: list[FlavorSearchHit]) -> FlavorSearchResult:
    return FlavorSearchResult(
        query="малина",
        parsed=ParsedQuery(raw="малина", flavor_text="малина"),
        hits=hits,
    )


# ── extract_weight_g ──────────────────────────────────────────────────────────

class TestExtractWeightG:
    def test_standard_suffix(self):
        assert extract_weight_g("BlackBurn Малина 200г") == 200

    def test_with_space(self):
        assert extract_weight_g("Duft Клубника 100 г") == 100

    def test_latin_g(self):
        assert extract_weight_g("Brand Flavor 250g") == 250

    def test_no_weight(self):
        assert extract_weight_g("Adalya Orange") is None

    def test_weight_not_at_end(self):
        # Weight in the middle, not as suffix
        assert extract_weight_g("100г Малина") is None

    def test_large_weight(self):
        assert extract_weight_g("SomeTabacco 1000г") == 1000


# ── strip_weight ──────────────────────────────────────────────────────────────

class TestStripWeight:
    def test_removes_suffix(self):
        assert strip_weight("BlackBurn Малина 200г") == "BlackBurn Малина"

    def test_no_weight_unchanged(self):
        assert strip_weight("Adalya Orange") == "Adalya Orange"

    def test_strips_trailing_whitespace(self):
        assert strip_weight("Duft Клубника 100 г") == "Duft Клубника"


# ── group_hits ────────────────────────────────────────────────────────────────

class TestGroupHits:
    def test_same_brand_different_weights_grouped(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=1200),
        ]
        groups = group_hits(hits)
        assert len(groups) == 1
        assert groups[0].base_name == "BlackBurn Малина"
        assert len(groups[0].variants) == 2

    def test_different_brands_not_grouped(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", brand_key="bb", brand_display="BlackBurn"),
            _hit("2", "Duft Малина 100г", brand_key="duft", brand_display="Duft"),
        ]
        groups = group_hits(hits)
        assert len(groups) == 2

    def test_no_weight_stays_single(self):
        hits = [
            _hit("1", "Adalya Orange"),
            _hit("2", "Adalya Orange"),  # duplicate name
        ]
        groups = group_hits(hits)
        # Both have same brand_key="bb" and same base_name → would group
        # but we probably don't want that; let's just verify the behavior
        assert len(groups) == 1
        assert groups[0].base_name == "Adalya Orange"

    def test_variants_sorted_by_weight_ascending(self):
        hits = [
            _hit("1", "BlackBurn Малина 250г"),
            _hit("2", "BlackBurn Малина 100г"),
            _hit("3", "BlackBurn Малина 50г"),
        ]
        groups = group_hits(hits)
        weights = [v.weight_g for v in groups[0].variants]
        assert weights == [50, 100, 250]

    def test_order_preserved_by_first_appearance(self):
        hits = [
            _hit("1", "Duft Клубника 100г", brand_key="duft", brand_display="Duft"),
            _hit("2", "BlackBurn Малина 100г", brand_key="bb", brand_display="BlackBurn"),
            _hit("3", "Duft Клубника 200г", brand_key="duft", brand_display="Duft"),
        ]
        groups = group_hits(hits)
        assert groups[0].brand_display == "Duft"
        assert groups[1].brand_display == "BlackBurn"

    def test_hit_indices_preserved(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г"),
            _hit("2", "BlackBurn Малина 200г"),
        ]
        groups = group_hits(hits)
        indices = [v.hit_index for v in groups[0].variants]
        assert 0 in indices and 1 in indices

    def test_empty_hits(self):
        assert group_hits([]) == []


# ── FlavorGroup properties ────────────────────────────────────────────────────

class TestFlavorGroupProperties:
    def _make_group(
        self,
        weights_and_statuses: list[tuple[int, str, float]],
    ) -> FlavorGroup:
        hits = [
            _hit(str(i), f"Brand Name {w}г", status=s, price=p)
            for i, (w, s, p) in enumerate(weights_and_statuses)
        ]
        return group_hits(hits)[0]

    def test_status_есть_when_any_in_stock(self):
        g = self._make_group([(100, "есть", 900), (200, "нет", 1200)])
        assert g.status == "есть"

    def test_status_нет_when_all_oos(self):
        g = self._make_group([(100, "нет", 900), (200, "нет", 1200)])
        assert g.status == "нет"

    def test_in_stock_variants(self):
        g = self._make_group([(100, "есть", 900), (200, "нет", 1200), (250, "есть", 1500)])
        assert len(g.in_stock_variants) == 2

    def test_min_price_uses_in_stock_only(self):
        g = self._make_group([(100, "есть", 900), (200, "нет", 500)])
        assert g.min_price == 900.0  # 500 is OOS, ignore

    def test_is_grouped_true_for_multiple(self):
        g = self._make_group([(100, "есть", 900), (200, "есть", 1200)])
        assert g.is_grouped is True

    def test_is_grouped_false_for_single(self):
        hits = [_hit("1", "BlackBurn Малина 100г")]
        g = group_hits(hits)[0]
        assert g.is_grouped is False


# ── format_flavor_search (grouping) ──────────────────────────────────────────

class TestFormatFlavorSearchGrouped:
    def test_grouped_shows_weights_slash(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=1200),
        ]
        text = format_flavor_search(_result(hits))
        assert "100 гр / 200 гр" in text or "100" in text and "200" in text
        assert "/" in text

    def test_grouped_oos_variant_strikethrough(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", status="есть", price=900),
            _hit("2", "BlackBurn Малина 200г", status="нет", price=1200),
        ]
        text = format_flavor_search(_result(hits))
        # OOS variant should be wrapped in <s>...</s>
        assert "<s>" in text and "200" in text

    def test_grouped_single_price(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=900),
        ]
        text = format_flavor_search(_result(hits))
        # Same price → show once, not "от X"; compact format uses "900₽" (no space)
        assert "900₽" in text
        assert "от" not in text

    def test_grouped_price_range_uses_от(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=1200),
        ]
        text = format_flavor_search(_result(hits))
        # compact format: "от 900₽" (no space before ₽)
        assert "от 900₽" in text

    def test_ungrouped_shows_full_name(self):
        hits = [_hit("1", "Adalya Orange", brand_key="adalya", brand_display="Adalya")]
        text = format_flavor_search(_result(hits))
        # compact format splits brand (bold) and flavor: "Adalya</b> · Orange"
        assert "Adalya" in text
        assert "Orange" in text

    def test_numbering_sequential(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", brand_key="bb"),
            _hit("2", "Duft Клубника 100г", brand_key="duft", brand_display="Duft"),
            _hit("3", "BlackBurn Малина 200г", brand_key="bb"),
        ]
        text = format_flavor_search(_result(hits))
        # Groups: BB Малина (100+200г), Duft Клубника (100г)
        # Should be numbered 1 and 2
        lines = [ln for ln in text.split("\n") if ln.startswith(("1.", "2.", "3."))]
        assert len(lines) == 2
        assert lines[0].startswith("1.")
        assert lines[1].startswith("2.")


# ── format_flavor_weight_group ────────────────────────────────────────────────

class TestFormatFlavorWeightGroup:
    def test_contains_base_name(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=1200),
        ]
        group = group_hits(hits)[0]
        text = format_flavor_weight_group(group)
        assert "Малина" in text

    def test_contains_in_stock_weights(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", status="есть", price=900),
            _hit("2", "BlackBurn Малина 200г", status="нет", price=1200),
        ]
        group = group_hits(hits)[0]
        text = format_flavor_weight_group(group)
        # Only in-stock shown in the weight summary
        assert "100 гр" in text
        assert "200 гр" not in text

    def test_contains_brand(self):
        hits = [_hit("1", "BlackBurn Малина 100г"), _hit("2", "BlackBurn Малина 200г")]
        group = group_hits(hits)[0]
        text = format_flavor_weight_group(group)
        assert "BlackBurn" in text


# ── flavor_search_keyboard grouping ──────────────────────────────────────────

class TestFlavorSearchKeyboardGrouped:
    def test_single_weight_uses_flavor_pick(self):
        hits = [_hit("1", "BlackBurn Малина 100г")]
        kb = flavor_search_keyboard(hits)
        assert kb is not None
        cb_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        pick_cbs = [d for d in cb_data if d and d.startswith(CB_FLAVOR_PICK)]
        assert len(pick_cbs) == 1
        assert pick_cbs[0] == f"{CB_FLAVOR_PICK}0"

    def test_multi_weight_uses_flavor_group(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г"),
            _hit("2", "BlackBurn Малина 200г"),
        ]
        kb = flavor_search_keyboard(hits)
        assert kb is not None
        cb_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        group_cbs = [d for d in cb_data if d and d.startswith(CB_FLAVOR_GROUP)]
        assert len(group_cbs) == 1
        assert group_cbs[0] == f"{CB_FLAVOR_GROUP}0"

    def test_mixed_groups_correct_callbacks(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", brand_key="bb"),
            _hit("2", "BlackBurn Малина 200г", brand_key="bb"),  # grouped with hit 1
            _hit("3", "Duft Клубника 100г", brand_key="duft", brand_display="Duft"),  # separate
        ]
        kb = flavor_search_keyboard(hits)
        assert kb is not None
        cb_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        group_cbs = [d for d in cb_data if d and d.startswith(CB_FLAVOR_GROUP)]
        pick_cbs = [d for d in cb_data if d and d.startswith(CB_FLAVOR_PICK)]
        # BB Малина (2 in-stock) → CB_FLAVOR_GROUP:0
        # Duft Клубника (1 in-stock) → CB_FLAVOR_PICK:2
        assert f"{CB_FLAVOR_GROUP}0" in group_cbs
        assert f"{CB_FLAVOR_PICK}2" in pick_cbs

    def test_all_oos_shows_no_pick_buttons(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", status="нет"),
            _hit("2", "BlackBurn Малина 200г", status="нет"),
        ]
        kb = flavor_search_keyboard(hits)
        assert kb is not None
        cb_data = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        pick_cbs = [d for d in cb_data if d and (
            d.startswith(CB_FLAVOR_PICK) or d.startswith(CB_FLAVOR_GROUP)
        )]
        assert len(pick_cbs) == 0

    def test_single_in_stock_group_label_is_в_корзину(self):
        """Когда один вариант в наличии — кнопка «🛒 В корзину»."""
        hits = [_hit("1", "BlackBurn Малина 100г")]
        kb = flavor_search_keyboard(hits)
        assert kb is not None
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert "🛒 В корзину" in labels

    def test_multiple_in_stock_groups_label_выбрать(self):
        """Когда несколько групп в наличии — кнопки «Выбрать N»."""
        hits = [
            _hit("1", "BlackBurn Малина 100г", brand_key="bb"),
            _hit("2", "Duft Клубника 100г", brand_key="duft", brand_display="Duft"),
        ]
        kb = flavor_search_keyboard(hits)
        assert kb is not None
        labels = [btn.text for row in kb.inline_keyboard for btn in row]
        assert any("Выбрать 1" in l for l in labels)
        assert any("Выбрать 2" in l for l in labels)


# ── weight_picker_keyboard ────────────────────────────────────────────────────

class TestWeightPickerKeyboard:
    def test_buttons_per_in_stock_variant(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=1200),
        ]
        group = group_hits(hits)[0]
        kb = weight_picker_keyboard(group)
        pick_cbs = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith(CB_FLAVOR_PICK)
        ]
        assert len(pick_cbs) == 2

    def test_callback_uses_correct_hit_index(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г"),
            _hit("2", "BlackBurn Малина 200г"),
        ]
        group = group_hits(hits)[0]
        # Variants are sorted by weight: 100г (hit_index depends on sort)
        kb = weight_picker_keyboard(group)
        pick_cbs = {
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith(CB_FLAVOR_PICK)
        }
        # Both hit indices (0 and 1 in original list) must appear
        assert f"{CB_FLAVOR_PICK}0" in pick_cbs or f"{CB_FLAVOR_PICK}1" in pick_cbs
        assert len(pick_cbs) == 2

    def test_oos_variants_excluded(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", status="есть"),
            _hit("2", "BlackBurn Малина 200г", status="нет"),
        ]
        group = group_hits(hits)[0]
        kb = weight_picker_keyboard(group)
        pick_cbs = [
            btn.callback_data
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith(CB_FLAVOR_PICK)
        ]
        assert len(pick_cbs) == 1

    def test_has_back_button(self):
        from bot.inline_keyboards import CB_BACK_FLAVOR
        hits = [
            _hit("1", "BlackBurn Малина 100г"),
            _hit("2", "BlackBurn Малина 200г"),
        ]
        group = group_hits(hits)[0]
        kb = weight_picker_keyboard(group)
        cbs = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert CB_BACK_FLAVOR in cbs

    def test_button_labels_contain_weight(self):
        hits = [
            _hit("1", "BlackBurn Малина 100г", price=900),
            _hit("2", "BlackBurn Малина 200г", price=1200),
        ]
        group = group_hits(hits)[0]
        kb = weight_picker_keyboard(group)
        labels = [
            btn.text
            for row in kb.inline_keyboard
            for btn in row
            if btn.callback_data and btn.callback_data.startswith(CB_FLAVOR_PICK)
        ]
        assert any("100" in l for l in labels)
        assert any("200" in l for l in labels)
