"""Автогенерация словарей брендов и вкусов из каталога Oshisha."""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .auth import OshishaAuth
from .catalog import OshishaCatalog, parse_catalog_html
from .vocabulary import normalize_text

VOCAB_DIR = Path(__file__).resolve().parents[2] / "data" / "vocab"
GENERATED_DIR = VOCAB_DIR / "generated"
CACHE_PATH = VOCAB_DIR / "cache" / "sections_products.json"

# Разделы каталога, которые не являются брендами табака
SECTION_SKIP = frozenset(
    {
        "kalyannye_smesi",
        "product",
        "diskont",
        "ugol",
        "kalyany",
        "kalyany_elektronnye",
        "pod_sistemy",
        "komplektuyushchie",
        "zhidkosti",
        "sigarilly",
        "kuritelnyy_tabak",
        "stiki",
        "nyukhatelnyy_tabak",
        "trubochnyy_tabak",
        "sigarety",
        "sigary",
        "rasta",
        "zhevatelnyy_tabak",
        "napitochnye_osnovy",
        "aksessuary",
        "aksessuary_1",
        "blyudtsa_1",
        "alpha_hookah",
        "personal",
        "catalog",
    }
)

AROMA_PATTERN = re.compile(
    r"^(?P<prefix>.+?)\s+с\s+ароматом\s+(?P<flavor_ru>.+?)"
    r"(?:\s*\((?P<flavor_en>[^)]+)\))?\s*,\s*(?P<weight>\d+)",
    re.IGNORECASE,
)
PAREN_PATTERN = re.compile(
    r"^(?P<prefix>.+?)\s+(?P<flavor_ru>[^,(]+?)\s*\((?P<flavor_en>[^)]+)\)\s*,\s*(?P<weight>\d+)",
    re.IGNORECASE,
)
SECTION_LINK_RE = re.compile(r'href="(/catalog/([a-z0-9_]+)/)"', re.IGNORECASE)
SLUG_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass
class ParsedProductName:
    brand_line: str
    flavor_ru: str
    flavor_en: str | None
    weight: int | None
    raw: str


@dataclass
class BrandAccumulator:
    slug: str
    display_names: set[str] = field(default_factory=set)
    product_count: int = 0


@dataclass
class FlavorAccumulator:
    brand_key: str
    display_ru: str
    display_en: str | None
    site_terms: set[str] = field(default_factory=set)
    product_count: int = 0


def slug_to_key(slug: str) -> str:
    return slug.strip("_").replace("-", "_")


def flavor_to_key(brand_key: str, flavor_ru: str, flavor_en: str | None) -> str:
    base = flavor_en or flavor_ru
    norm = normalize_text(base)
    norm = re.sub(r"[^a-z0-9]+", "_", norm).strip("_")[:48]
    return f"{brand_key}_{norm}" if norm else f"{brand_key}_unknown"


def parse_product_name(name: str) -> ParsedProductName | None:
    name = name.strip()
    m = AROMA_PATTERN.match(name)
    if not m:
        m = PAREN_PATTERN.match(name)
    if not m:
        return None

    flavor_ru = m.group("flavor_ru").strip()
    flavor_en = (m.groupdict().get("flavor_en") or "").strip() or None
    weight = int(m.group("weight"))
    prefix = m.group("prefix").strip()

    # убрать лишние хвосты линейки: «360 Крепкая», «Undercoal», «HiT»
    brand_line = re.sub(r"\s+\d{2,4}\s+", " ", prefix)
    brand_line = brand_line.split(" с ароматом")[0].strip()

    return ParsedProductName(
        brand_line=brand_line,
        flavor_ru=flavor_ru,
        flavor_en=flavor_en,
        weight=weight,
        raw=name,
    )


def discover_section_slugs(html: str) -> list[str]:
    found: dict[str, int] = {}
    for _path, slug in SECTION_LINK_RE.findall(html):
        if slug in SECTION_SKIP or not SLUG_RE.match(slug):
            continue
        found[slug] = found.get(slug, 0) + 1
    return sorted(found.keys())


def _default_aliases(display: str, slug: str) -> list[str]:
    aliases = {normalize_text(slug.replace("_", " ")), normalize_text(slug)}
    parts = display.split()
    if parts:
        aliases.add(normalize_text(parts[0]))
    if len(parts) >= 2:
        aliases.add(normalize_text(" ".join(parts[:2])))
    return sorted(a for a in aliases if len(a) >= 2)


class VocabBuilder:
    def __init__(self, auth: OshishaAuth, *, delay_sec: float = 0.35) -> None:
        self.auth = auth
        self.catalog = OshishaCatalog(auth, load_vocab=False)
        self.delay_sec = delay_sec
        self.brands: dict[str, BrandAccumulator] = {}
        self.flavors: dict[str, FlavorAccumulator] = {}

    def _sleep(self) -> None:
        if self.delay_sec > 0:
            time.sleep(self.delay_sec)

    def discover_sections(self, root_path: str = "/catalog/kalyannye_smesi/") -> list[str]:
        resp = self.auth.get(root_path)
        resp.raise_for_status()
        return discover_section_slugs(resp.text)

    def load_cache(self) -> dict[str, list[str]]:
        if not CACHE_PATH.exists():
            return {}
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))

    def save_cache(self, data: dict[str, list[str]]) -> None:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def scan_section(self, slug: str, product_names: list[str] | None = None) -> list[str]:
        """Собрать названия товаров в разделе (или использовать переданный кэш)."""
        if product_names is not None:
            names = product_names
        else:
            path = f"/catalog/{slug}/"
            names = []
            page = self.catalog.fetch_section(path)
            names.extend(p.name for p in page.products)
            total = page.total_pages or 1
            for num in range(2, total + 1):
                self._sleep()
                names.extend(p.name for p in self.catalog.fetch_section(path, page=num).products)
            self._sleep()

        brand_key = slug_to_key(slug)
        if brand_key not in self.brands:
            self.brands[brand_key] = BrandAccumulator(slug=slug)

        acc = self.brands[brand_key]
        acc.product_count += len(names)

        for name in names:
            parsed = parse_product_name(name)
            if not parsed:
                continue

            acc.display_names.add(parsed.brand_line)

            fkey = flavor_to_key(brand_key, parsed.flavor_ru, parsed.flavor_en)
            if fkey not in self.flavors:
                self.flavors[fkey] = FlavorAccumulator(
                    brand_key=brand_key,
                    display_ru=parsed.flavor_ru,
                    display_en=parsed.flavor_en,
                )
            fl = self.flavors[fkey]
            fl.product_count += 1
            fl.site_terms.add(parsed.flavor_ru)
            if parsed.flavor_en:
                fl.site_terms.add(parsed.flavor_en)

        return names

    def scan_all(
        self,
        slugs: list[str],
        *,
        cache: dict[str, list[str]] | None = None,
        use_cache: bool = True,
    ) -> dict[str, list[str]]:
        cache = cache if cache is not None else (self.load_cache() if use_cache else {})
        for i, slug in enumerate(slugs, 1):
            if use_cache and slug in cache:
                self.scan_section(slug, cache[slug])
                continue
            print(f"[{i}/{len(slugs)}] {slug}...")
            names = self.scan_section(slug)
            cache[slug] = names
            if i % 10 == 0:
                self.save_cache(cache)
        self.save_cache(cache)
        return cache

    def to_brands_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, acc in sorted(self.brands.items()):
            if acc.product_count == 0:
                continue
            names = sorted(acc.display_names, key=len)
            display = names[0] if names else key.replace("_", " ").title()
            site_names = sorted({n for n in names if len(n) <= 40})[:6]
            if not site_names:
                site_names = [display]

            out[key] = {
                "display": display,
                "catalog_section": f"/catalog/{acc.slug}/",
                "site_names": site_names,
                "aliases": _default_aliases(display, acc.slug),
                "auto_generated": True,
                "product_count": acc.product_count,
            }
        return out

    def to_flavors_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, fl in sorted(self.flavors.items()):
            if fl.product_count == 0:
                continue
            terms = sorted(fl.site_terms, key=len)[:8]
            hints: list[str] = []
            brand = self.brands.get(fl.brand_key)
            if brand and terms:
                bn = sorted(brand.display_names, key=len)[0] if brand.display_names else fl.brand_key
                hints.append(f"{bn} {terms[0]}")

            out[key] = {
                "display": fl.display_ru,
                "aliases": [normalize_text(fl.display_ru)],
                "site_terms": terms[:6],
                "search_hints": hints[:2],
                "brands": [fl.brand_key],
                "auto_generated": True,
                "product_count": fl.product_count,
            }
            if fl.display_en:
                out[key]["aliases"].append(normalize_text(fl.display_en))
            out[key]["aliases"] = sorted(set(out[key]["aliases"]))

        return out


def merge_vocab(
    generated: dict[str, Any],
    manual: dict[str, Any],
    *,
    list_fields: tuple[str, ...] = ("aliases", "site_names", "search_hints", "brands"),
) -> dict[str, Any]:
    """Слияние: manual дополняет и перекрывает generated."""
    merged = dict(generated)
    for key, man in manual.items():
        if key not in merged:
            merged[key] = dict(man)
            continue
        gen = merged[key]
        row = {**gen, **{k: v for k, v in man.items() if k not in list_fields}}
        for field in list_fields:
            if field in man or field in gen:
                vals = list(gen.get(field, [])) + list(man.get(field, []))
                # уникальность с сохранением порядка
                seen: set[str] = set()
                uniq: list[Any] = []
                for v in vals:
                    nk = normalize_text(str(v)) if isinstance(v, str) else str(v)
                    if nk not in seen:
                        seen.add(nk)
                        uniq.append(v)
                row[field] = uniq
        row["auto_generated"] = gen.get("auto_generated", False)
        if man.get("aliases"):
            row["manual_override"] = True
        merged[key] = row
    return merged


def write_vocab_files(brands: dict[str, Any], flavors: dict[str, Any]) -> None:
    """Записать только в data/vocab/generated/ (ручные overrides не трогаем)."""
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    (GENERATED_DIR / "brands.json").write_text(
        json.dumps(brands, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (GENERATED_DIR / "flavors.json").write_text(
        json.dumps(flavors, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
