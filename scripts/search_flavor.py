#!/usr/bin/env python3
"""CLI: поиск табака по вкусу."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv

from bot.formatters import format_flavor_search  # noqa: E402
from oshisha.service import OshishaService  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")
    query = " ".join(sys.argv[1:]).strip()
    if not query:
        print("Использование: python scripts/search_flavor.py малина 200")
        return 1

    service = OshishaService()
    try:
        result = service.search_flavor(query, limit=20)
        text = format_flavor_search(result)
        # plain text for console
        print(text.replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""))
    finally:
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
