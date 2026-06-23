"""Словари брендов и вкусов + нечёткое сопоставление."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

VOCAB_DIR = Path(__file__).resolve().parents[2] / "data" / "vocab"
GENERATED_VOCAB_DIR = VOCAB_DIR / "generated"


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not str(k).startswith("_") and isinstance(v, dict)}


def _merge_vocab_dicts(generated: dict[str, Any], manual: dict[str, Any]) -> dict[str, Any]:
    """generated + manual (manual перекрывает поля, списки объединяются)."""
    list_fields = ("aliases", "site_names", "search_hints", "brands")
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
                seen: set[str] = set()
                uniq: list[Any] = []
                for v in vals:
                    nk = normalize_text(str(v)) if isinstance(v, str) else str(v)
                    if nk not in seen:
                        seen.add(nk)
                        uniq.append(v)
                row[field] = uniq
        merged[key] = row
    return merged


def normalize_text(value: str) -> str:
    text = value.lower().strip()
    text = text.replace("ё", "е")
    text = re.sub(r"[^\w\s\-]+", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s*-\s*", " ", text)
    return re.sub(r"\s+", " ", text)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class BrandEntry:
    key: str
    display: str
    catalog_section: str | None
    site_names: list[str]
    aliases: list[str]


@dataclass
class FlavorEntry:
    key: str
    display: str
    aliases: list[str]
    site_terms: list[str]
    search_hints: list[str]
    brands: list[str]


@dataclass
class Vocabulary:
    brands: dict[str, BrandEntry]
    flavors: dict[str, FlavorEntry]
    _brand_alias_index: list[tuple[str, str]] = field(default_factory=list, repr=False)
    _flavor_alias_index: list[tuple[str, str]] = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, vocab_dir: Path | None = None) -> Vocabulary:
        root = vocab_dir or VOCAB_DIR
        gen_dir = root / "generated" if (root / "generated").exists() else GENERATED_VOCAB_DIR

        brands_gen = _load_json_dict(gen_dir / "brands.json")
        flavors_gen = _load_json_dict(gen_dir / "flavors.json")
        brands_manual = _load_json_dict(root / "brands.json")
        brands_manual = _merge_vocab_dicts(brands_manual, _load_json_dict(root / "brands.manual.json"))
        flavors_manual = _load_json_dict(root / "flavors.json")
        flavors_manual = _merge_vocab_dicts(flavors_manual, _load_json_dict(root / "flavors.manual.json"))

        brands_raw = _merge_vocab_dicts(brands_gen, brands_manual) if brands_gen else brands_manual
        flavors_raw = _merge_vocab_dicts(flavors_gen, flavors_manual) if flavors_gen else flavors_manual

        brands: dict[str, BrandEntry] = {}
        for key, data in brands_raw.items():
            if "display" not in data:
                continue
            brands[key] = BrandEntry(
                key=key,
                display=data["display"],
                catalog_section=data.get("catalog_section"),
                site_names=list(data.get("site_names", [])),
                aliases=[normalize_text(a) for a in data.get("aliases", [])],
            )

        flavors: dict[str, FlavorEntry] = {}
        for key, data in flavors_raw.items():
            display = data.get("display") or data.get("aliases", [""])[0] if data.get("aliases") else key
            if not display:
                continue
            flavors[key] = FlavorEntry(
                key=key,
                display=str(display),
                aliases=[normalize_text(a) for a in data.get("aliases", [])],
                site_terms=list(data.get("site_terms", [])),
                search_hints=list(data.get("search_hints", [])),
                brands=list(data.get("brands", [])),
            )

        vocab = cls(brands=brands, flavors=flavors)
        vocab._build_indexes()
        return vocab

    def _build_indexes(self) -> None:
        brand_idx: list[tuple[str, str]] = []
        for key, brand in self.brands.items():
            for alias in brand.aliases:
                brand_idx.append((alias, key))
            for name in brand.site_names:
                brand_idx.append((normalize_text(name), key))
        brand_idx.sort(key=lambda x: -len(x[0]))
        self._brand_alias_index = brand_idx

        flavor_idx: list[tuple[str, str]] = []
        for key, flavor in self.flavors.items():
            for alias in flavor.aliases:
                flavor_idx.append((alias, key))
            for term in flavor.site_terms:
                flavor_idx.append((normalize_text(term), key))
        flavor_idx.sort(key=lambda x: -len(x[0]))
        self._flavor_alias_index = flavor_idx

    def match_brand(self, text: str, *, typo_threshold: float = 0.82) -> tuple[str | None, str]:
        """
        Найти бренд в начале строки.
        Возвращает (brand_key, остаток строки).
        """
        norm = normalize_text(text)
        if not norm:
            return None, text

        for alias, key in self._brand_alias_index:
            if norm == alias:
                rest = norm[len(alias) :].strip()
                return key, rest
            if norm.startswith(alias + " "):
                return key, norm[len(alias) + 1 :].strip()

        # нечётко: первое слово или два
        words = norm.split()
        for size in (2, 1):
            if len(words) < size:
                continue
            chunk = " ".join(words[:size])
            best_key = None
            best_score = 0.0
            for alias, key in self._brand_alias_index:
                score = _similar(chunk, alias)
                if score > best_score:
                    best_score = score
                    best_key = key
            if best_key and best_score >= typo_threshold:
                rest = " ".join(words[size:])
                return best_key, rest

        return None, norm

    def match_flavors(
        self,
        text: str,
        *,
        brand_key: str | None = None,
        typo_threshold: float = 0.78,
    ) -> list[str]:
        """Подобрать ключи вкусов (приоритет длинным фразам вроде «деревенская вишня»)."""
        norm = normalize_text(text)
        if not norm:
            return []

        best: dict[str, float] = {}
        for alias, key in self._flavor_alias_index:
            flavor = self.flavors[key]
            if brand_key and flavor.brands and brand_key not in flavor.brands:
                continue
            if len(alias) < 3:
                continue

            score = 0.0
            if norm == alias:
                score = 1.0
            elif alias in norm:
                score = 0.9 + min(0.09, len(alias) / max(len(norm), 1) * 0.1)
            elif norm in alias:
                score = 0.88
            else:
                sim = _similar(norm, alias)
                if sim >= typo_threshold:
                    score = sim

            if score > 0:
                best[key] = max(best.get(key, 0.0), score)

        if not best:
            for token in norm.split():
                if len(token) < 4:
                    continue
                for alias, key in self._flavor_alias_index:
                    flavor = self.flavors[key]
                    if brand_key and flavor.brands and brand_key not in flavor.brands:
                        continue
                    if token in alias.split():
                        best[key] = max(best.get(key, 0.0), 0.72)

        if not best:
            return []

        top = max(best.values())
        if top == 1.0:
            # Точное совпадение: возвращаем только ключи с точным матчем,
            # чтобы generic-подстроки («виноград» внутри «виноградная газировка»)
            # не вытесняли специфичный вкус («grape_soda»).
            return [k for k, s in sorted(best.items(), key=lambda x: -x[1]) if s == 1.0]
        cutoff = max(typo_threshold, top - 0.12)
        return [k for k, s in sorted(best.items(), key=lambda x: -x[1]) if s >= cutoff]

    def build_search_terms(
        self,
        *,
        brand_key: str | None,
        flavor_keys: list[str],
        flavor_text: str,
        weight: int | None,
    ) -> list[str]:
        """Собрать поисковые запросы для сайта."""
        queries: list[str] = []
        brand_names: list[str] = []
        if brand_key and brand_key in self.brands:
            brand = self.brands[brand_key]
            brand_names = brand.site_names[:2]

        flavor_terms: list[str] = []
        extra_hints: list[str] = []
        for fk in flavor_keys:
            flavor = self.flavors[fk]
            flavor_terms.extend(flavor.site_terms[:2])
            extra_hints.extend(flavor.search_hints[:2])
        if flavor_text and not flavor_terms:
            flavor_terms.append(flavor_text)

        def _pairs(bn: str, ft: str) -> list[str]:
            base = [f"{bn} {ft}".strip(), f"{ft} {bn}".strip()]
            if weight:
                return base + [f"{bn} {ft} {weight}".strip(), f"{ft} {bn} {weight} гр".strip()]
            return base

        if brand_names and flavor_terms:
            for bn in brand_names[:2]:
                for ft in flavor_terms[:3]:
                    queries.extend(_pairs(bn, ft))
        elif flavor_terms:
            for ft in flavor_terms[:4]:
                queries.append(ft)
                if weight:
                    queries.append(f"{ft} {weight}")
        elif brand_names and flavor_text:
            for bn in brand_names[:2]:
                queries.extend(_pairs(bn, flavor_text))
        elif flavor_text:
            queries.append(flavor_text)
            if weight:
                queries.append(f"{flavor_text} {weight}")

        if not queries and brand_names:
            queries.append(brand_names[0])

        queries = extra_hints + queries

        # дедуп (сначала варианты без граммовки — они уже в начале _pairs)
        seen: set[str] = set()
        out: list[str] = []
        for q in queries:
            k = normalize_text(q)
            if k not in seen:
                seen.add(k)
                out.append(q)
        return out[:12]

    def section_for(self, brand_key: str | None, flavor_keys: list[str]) -> str | None:
        if brand_key and brand_key in self.brands:
            return self.brands[brand_key].catalog_section
        for fk in flavor_keys:
            flavor = self.flavors.get(fk)
            if flavor and flavor.brands:
                bk = flavor.brands[0]
                return self.brands[bk].catalog_section
        return None


@lru_cache(maxsize=1)
def get_vocabulary() -> Vocabulary:
    return Vocabulary.load()
