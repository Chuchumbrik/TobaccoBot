"""Группировка результатов поиска по граммовке.

Объединяет варианты одного табака с разными граммовками в FlavorGroup,
чтобы отображать их одной строкой (напр. «100 / 200 / 250 гр») и давать
пользователю выбор перед добавлением в корзину.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from oshisha.flavor_search import FlavorSearchHit

# Суффикс граммовки в конце названия:
# «200г», «100 г», «250g», «100гр», «100 гр», «100гр.», «200 гр.»
# [гgГG]   — сама буква г/g
# [рrРR]?  — необязательное «р» (гр, gr)
# [.,]?    — необязательная точка или запятая после единицы
_WEIGHT_SUFFIX_RE = re.compile(
    r"\s+(\d+)\s*[гgГG][рrРR]?[.,]?\s*$",
    re.IGNORECASE | re.UNICODE,
)


def extract_weight_g(name: str) -> int | None:
    """Извлекает граммовку из конца названия (например «200г» → 200).

    Возвращает None, если суффикс граммовки не найден.
    """
    m = _WEIGHT_SUFFIX_RE.search(name)
    return int(m.group(1)) if m else None


def strip_weight(name: str) -> str:
    """Возвращает название без суффикса граммовки.

    Пример: «BlackBurn Малина 200г» → «BlackBurn Малина».
    Также убирает разделительные запятые/пробелы перед числом:
    «Banger Sexy, 100гр.» → «Banger Sexy».
    """
    result = _WEIGHT_SUFFIX_RE.sub("", name).strip()
    # Убрать запятую (и пробелы) в конце, если осталась как разделитель
    return result.rstrip(", ").strip()


@dataclass
class WeightVariant:
    """Один весовой вариант продукта внутри группы."""

    hit_index: int        # индекс в исходном списке FlavorSearchHit
    hit: FlavorSearchHit
    weight_g: int | None  # граммовка (None если не удалось определить)


@dataclass
class FlavorGroup:
    """Группа вариантов одного табака с разными граммовками."""

    base_name: str                                   # название без граммовки
    brand_display: str | None                        # отображаемое имя бренда
    variants: list[WeightVariant] = field(default_factory=list)

    @property
    def in_stock_variants(self) -> list[WeightVariant]:
        return [v for v in self.variants if v.hit.status == "есть"]

    @property
    def status(self) -> str:
        return "есть" if self.in_stock_variants else "нет"

    @property
    def min_price(self) -> float | None:
        prices = [
            v.hit.product.price
            for v in self.in_stock_variants
            if v.hit.product.price is not None
        ]
        return min(prices) if prices else None

    @property
    def is_grouped(self) -> bool:
        """True, если группа содержит несколько позиций (разные граммовки)."""
        return len(self.variants) > 1


def _group_key(hit: FlavorSearchHit) -> str:
    """Ключ группировки: нормализованные бренд + базовое название."""
    brand = (hit.brand_key or "").lower().strip()
    bname = strip_weight(hit.product.name).lower().strip()
    return f"{brand}|{bname}"


def group_hits(hits: list[FlavorSearchHit]) -> list[FlavorGroup]:
    """Группирует хиты по базовому названию + ключу бренда.

    Порядок групп соответствует первому появлению в hits.
    Варианты внутри каждой группы отсортированы по весу (по возрастанию).
    Хиты без суффикса граммовки образуют отдельную группу (по одному).
    """
    groups_map: dict[str, FlavorGroup] = {}
    order: list[str] = []

    for i, hit in enumerate(hits):
        key = _group_key(hit)
        w = extract_weight_g(hit.product.name) or hit.matched_weight_g
        variant = WeightVariant(hit_index=i, hit=hit, weight_g=w)

        if key not in groups_map:
            groups_map[key] = FlavorGroup(
                base_name=strip_weight(hit.product.name),
                brand_display=hit.brand_display,
                variants=[],
            )
            order.append(key)
        groups_map[key].variants.append(variant)

    # Sort variants by weight within each group
    for g in groups_map.values():
        g.variants.sort(key=lambda v: v.weight_g or 0)

    return [groups_map[k] for k in order]
