#!/usr/bin/env python3
"""CLI: сравнение поиска или списка на нескольких сайтах.

Примеры:
  python scripts/compare_sites.py search "малина 200"
  python scripts/compare_sites.py list data/sample_list.txt
  SHOP_SITES=oshisha,stub python scripts/compare_sites.py search "клубника"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from shops.format_compare import format_compare_list, format_compare_search
from shops.hub import ShopHub


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение магазинов (SHOP_SITES)")
    parser.add_argument(
        "mode",
        choices=("search", "list"),
        help="search — по вкусу; list — проверка строк",
    )
    parser.add_argument("arg", help="запрос или путь к файлу со списком")
    parser.add_argument("--limit", type=int, default=10, help="лимит на сайт")
    parser.add_argument(
        "--sites",
        default="",
        help="через запятую (иначе SHOP_SITES из .env)",
    )
    args = parser.parse_args()

    site_ids = (
        [s.strip() for s in args.sites.split(",") if s.strip()]
        if args.sites
        else None
    )
    hub = ShopHub(site_ids=site_ids) if site_ids else ShopHub.from_env()
    print(f"Сайты: {', '.join(f'{a} ({b})' for a, b in hub.list_sites())}\n")

    if args.mode == "search":
        compare = hub.compare_search_flavor(args.arg, limit=args.limit)
        # plain text for terminal
        text = format_compare_search(compare)
        print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
        return 0

    path = Path(args.arg)
    if not path.is_file():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    compare = hub.compare_check_list(lines)
    text = format_compare_list(compare)
    print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
