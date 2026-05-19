#!/usr/bin/env python3
"""Тестовый запрос каталога Oshisha."""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oshisha import OshishaAuth, OshishaAuthError  # noqa: E402
from oshisha.catalog import OshishaCatalog  # noqa: E402

DEFAULT_SECTION = "/catalog/nash_1/"


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def main() -> int:
    load_env()
    section = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SECTION

    email = os.environ.get("OSHISHA_EMAIL")
    password = os.environ.get("OSHISHA_PASSWORD")
    base_url = os.environ.get("OSHISHA_BASE_URL", "https://oshisha.cc")
    session_path = ROOT / "data" / "sessions" / "oshisha.json"

    with OshishaAuth(base_url, session_file=session_path) as auth:
        if not auth.is_authenticated:
            if not email or not password:
                print("Нет сессии и нет OSHISHA_EMAIL/OSHISHA_PASSWORD в .env")
                return 1
            try:
                auth.login_email(email, password)
                print("Вход выполнен, сессия сохранена.")
            except OshishaAuthError as exc:
                print(f"Ошибка входа: {exc}")
                return 1

        catalog = OshishaCatalog(auth)
        page = catalog.fetch_section(section)

    print(f"Раздел: {page.section_title}")
    print(f"URL: {page.url}")
    print(f"Товаров на странице: {len(page.products)}")
    if page.page:
        print(f"Страница: {page.page} / {page.total_pages or '?'}")

    if page.products:
        sample = page.products[0]
        print("\nПример товара (нормализованные поля):")
        print(json.dumps(sample.__dict__, ensure_ascii=False, indent=2, default=str)[:2000])

        print("\nКлючи PRODUCT в сыром ответе Bitrix:")
        raw_product = sample.raw.get("product", {})
        for key in sorted(raw_product.keys()):
            val = raw_product[key]
            preview = str(val)[:80] + ("…" if len(str(val)) > 80 else "")
            print(f"  {key}: {preview}")

    out = ROOT / "data" / "catalog_sample.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "url": page.url,
        "section_title": page.section_title,
        "count": len(page.products),
        "products": [
            {
                "id": p.id,
                "name": p.name,
                "url": p.url,
                "can_buy": p.can_buy,
                "max_quantity": p.max_quantity,
                "price": p.price,
                "base_price": p.base_price,
                "currency": p.currency,
            }
            for p in page.products[:5]
        ],
        "raw_product_keys": sorted(page.products[0].raw.get("product", {}).keys()) if page.products else [],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nСохранено: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
