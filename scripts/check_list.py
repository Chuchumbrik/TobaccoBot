#!/usr/bin/env python3
"""
Проверка списка табаков на oshisha.cc.

Использование:
  python scripts/check_list.py "NАШ Корохо 30" "BlackBurn mango 200"
  python scripts/check_list.py --file list.txt
  type list.txt | python scripts/check_list.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oshisha import OshishaAuth, OshishaAuthError  # noqa: E402
from oshisha.catalog import OshishaCatalog, ProductCheckResult  # noqa: E402


def load_names(args: argparse.Namespace) -> list[str]:
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]

    names = list(args.names)
    if not names and not sys.stdin.isatty():
        names = [line.strip() for line in sys.stdin if line.strip()]
    return names


def format_result(r: ProductCheckResult) -> str:
    parsed_line = ""
    if r.parsed and r.parsed.get("summary"):
        parsed_line = f"\n    ({r.parsed['summary']})"

    if r.status == "не найден":
        return f"[?] {r.query} — не найден{parsed_line}"

    icon = "[+]" if r.status == "есть" else "[-]"
    qty = f", остаток {int(r.max_quantity)}" if r.max_quantity is not None else ""
    price = f", {int(r.price)} ₽" if r.price is not None else ""
    packs = f", заказ ×{r.pack_count}" if r.pack_count > 1 else ""
    weight_note = ""
    if (
        r.requested_weight_g
        and r.matched_weight_g
        and r.requested_weight_g != r.matched_weight_g
    ):
        weight_note = f" (на сайте {r.matched_weight_g}г, запрошено {r.requested_weight_g}г)"
    return (
        f"{icon} {r.query} — {r.status}{weight_note}{price}{qty}{packs}\n"
        f"    → {r.matched_name}{parsed_line}"
    )


def main() -> int:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Проверка наличия по списку (Oshisha)")
    parser.add_argument("names", nargs="*", help="Названия товаров")
    parser.add_argument("--file", "-f", help="Файл со списком (по одному названию на строку)")
    args = parser.parse_args()

    names = load_names(args)
    if not names:
        print("Укажите названия аргументами, --file или stdin")
        return 1

    email = os.environ.get("OSHISHA_EMAIL")
    password = os.environ.get("OSHISHA_PASSWORD")
    base_url = os.environ.get("OSHISHA_BASE_URL", "https://oshisha.cc")
    session_path = ROOT / "data" / "sessions" / "oshisha.json"

    with OshishaAuth(base_url, session_file=session_path) as auth:
        if not auth.is_authenticated:
            if not email or not password:
                print("Нет сессии. Задайте OSHISHA_EMAIL/OSHISHA_PASSWORD в .env")
                return 1
            try:
                auth.login_email(email, password)
            except OshishaAuthError as exc:
                print(f"Ошибка входа: {exc}")
                return 1

        catalog = OshishaCatalog(auth)
        results = catalog.check_products(names)

    # UTF-8 для консоли Windows
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    print(f"Проверено: {len(results)}\n")
    for result in results:
        print(format_result(result))
        print()

    out = ROOT / "data" / "check_results.json"
    import json

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
