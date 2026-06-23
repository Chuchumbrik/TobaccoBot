"""Регрессионные тесты для _parse_brand_structured_list в routing.py."""

from __future__ import annotations

import pytest

from bot.handlers.routing import _parse_brand_structured_list


class TestParseBrandStructuredList:
    def test_plain_list_passthrough(self):
        text = "Малина\nКлубника\nЧерника"
        assert _parse_brand_structured_list(text) == ["Малина", "Клубника", "Черника"]

    def test_brand_header_prepended(self):
        text = "Палитра:\nДабл фрост\nГрейпфрут"
        result = _parse_brand_structured_list(text)
        assert result == ["Палитра Дабл фрост", "Палитра Грейпфрут"]

    def test_separator_lines_filtered(self):
        text = "Сартир:\nВиноградная газировка\n__________\nЧерная смородина"
        result = _parse_brand_structured_list(text)
        assert result == ["Сартир Виноградная газировка", "Сартир Черная смородина"]

    def test_dash_separator_filtered(self):
        text = "Троф:\nИталия 3х\n—————————————\nКола"
        result = _parse_brand_structured_list(text)
        assert result == ["Троф Италия 3х", "Троф Кола"]

    def test_category_word_tabak_filtered(self):
        text = "Табак\nСартир:\nВкус1"
        result = _parse_brand_structured_list(text)
        assert result == ["Сартир Вкус1"]

    def test_multiple_brands(self):
        text = "Сартир:\nВиноградная газировка\nЧерная смородина\nТроф:\nИталия 3х\nКола"
        result = _parse_brand_structured_list(text)
        assert result == [
            "Сартир Виноградная газировка",
            "Сартир Черная смородина",
            "Троф Италия 3х",
            "Троф Кола",
        ]

    def test_full_screenshot_example(self):
        text = (
            "Табак\n"
            "Сартир:\n"
            "Виноградная газировка\n"
            "Черная смородина\n"
            "Бекон\n"
            "Кактус\n"
            "Сакура\n"
            "___________\n"
            "\n"
            "Троф:\n"
            "Италия 3х\n"
            "Кола\n"
            "Красная смородина\n"
            "Коннектикут\n"
            "___________\n"
            "\n"
            "Палитра:\n"
            "Дабл фрост\n"
            "Грейпфрут\n"
            "Скитлз"
        )
        result = _parse_brand_structured_list(text)
        assert result == [
            "Сартир Виноградная газировка",
            "Сартир Черная смородина",
            "Сартир Бекон",
            "Сартир Кактус",
            "Сартир Сакура",
            "Троф Италия 3х",
            "Троф Кола",
            "Троф Красная смородина",
            "Троф Коннектикут",
            "Палитра Дабл фрост",
            "Палитра Грейпфрут",
            "Палитра Скитлз",
        ]

    def test_empty_brand_header_ignored(self):
        """Строка ':' без бренда не сбрасывает контекст бренда."""
        text = "Троф:\nКола\n:\nКоннектикут"
        result = _parse_brand_structured_list(text)
        assert result == ["Троф Кола", "Троф Коннектикут"]

    def test_empty_lines_skipped(self):
        text = "Малина\n\n\nКлубника"
        assert _parse_brand_structured_list(text) == ["Малина", "Клубника"]
