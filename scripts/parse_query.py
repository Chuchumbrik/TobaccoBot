#!/usr/bin/env python3
"""Показать, как бот разбирает строку запроса (без запроса к сайту)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oshisha.query_parser import parse_query  # noqa: E402
from oshisha.vocabulary import get_vocabulary  # noqa: E402


def main() -> int:
    vocab = get_vocabulary()
    lines = sys.argv[1:] or Path(ROOT / "data" / "user_list.txt").read_text(encoding="utf-8").splitlines()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = parse_query(line, vocab)
        terms = vocab.build_search_terms(
            brand_key=p.brand_key,
            flavor_keys=p.flavor_keys or [],
            flavor_text=p.flavor_text,
            weight=p.weight_grams,
        )
        print(f"{line}")
        print(f"  → {p.summary()}")
        print(f"  → поиск: {terms[:4]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
