"""Разбор строки запроса: бренд, вкус, граммовка, количество упаковок."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .vocabulary import Vocabulary, get_vocabulary, normalize_text

# 200 3х / 200 х2 / x3
PACK_SUFFIX_RE = re.compile(
    r"(?:^|\s)(\d+)\s*[xх×]\s*$|(?:^|\s)[xх×]\s*(\d+)\s*$",
    re.IGNORECASE,
)
WEIGHT_EXPLICIT_RE = re.compile(r"(\d+)\s*(?:гр?|g|gr)\b", re.IGNORECASE)
WEIGHT_TRAILING_RE = re.compile(r"\b(\d{2,4})\s*$")


@dataclass
class ParsedQuery:
    raw: str
    brand_key: str | None = None
    brand_display: str | None = None
    flavor_keys: list[str] = field(default_factory=list)
    flavor_text: str = ""
    weight_grams: int | None = None
    pack_count: int = 1

    def summary(self) -> str:
        parts = []
        if self.brand_display:
            parts.append(f"бренд={self.brand_display}")
        if self.flavor_keys:
            parts.append(f"вкусы={','.join(self.flavor_keys)}")
        elif self.flavor_text:
            parts.append(f"вкус={self.flavor_text}")
        if self.weight_grams:
            parts.append(f"{self.weight_grams}г")
        if self.pack_count > 1:
            parts.append(f"×{self.pack_count}")
        return ", ".join(parts) if parts else self.raw


def _extract_pack_count(text: str) -> tuple[str, int]:
    pack = 1
    m = PACK_SUFFIX_RE.search(text)
    if not m:
        return text, pack
    pack = int(m.group(1) or m.group(2))
    return text[: m.start()].strip(), pack


def _extract_weight(text: str) -> tuple[str, int | None]:
    m = WEIGHT_EXPLICIT_RE.search(text)
    if m:
        weight = int(m.group(1))
        text = (text[: m.start()] + text[m.end() :]).strip()
        return text, weight

    m = WEIGHT_TRAILING_RE.search(text)
    if m:
        weight = int(m.group(1))
        text = text[: m.start()].strip()
        return text, weight

    return text, None


def parse_query(line: str, vocab: Vocabulary | None = None) -> ParsedQuery:
    """
    Разобрать строку из списка бота.

    Примеры:
      «Бб - черешневый сок 200гр» → BlackBurn, cherry_garden, 200г
      «Сарма - деревенская вишня 200 3х» → САРМА, country_cherry, 200г, ×3
      «Арбуз-дыня 200гр» → без бренда, watermelon_melon, 200г
    """
    vocab = vocab or get_vocabulary()
    raw = line.strip()
    text = normalize_text(raw)

    text, pack_count = _extract_pack_count(text)
    text, weight = _extract_weight(text)

    brand_key, remainder = vocab.match_brand(text)
    brand_display = vocab.brands[brand_key].display if brand_key else None

    flavor_keys = vocab.match_flavors(remainder, brand_key=brand_key)
    flavor_text = remainder if not flavor_keys else ""

    return ParsedQuery(
        raw=raw,
        brand_key=brand_key,
        brand_display=brand_display,
        flavor_keys=flavor_keys,
        flavor_text=flavor_text,
        weight_grams=weight,
        pack_count=pack_count,
    )
