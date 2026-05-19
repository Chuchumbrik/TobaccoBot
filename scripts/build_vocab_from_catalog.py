#!/usr/bin/env python3
"""
Сканирует каталог Oshisha и генерирует словари брендов/вкусов.

Примеры:
  python scripts/build_vocab_from_catalog.py
  python scripts/build_vocab_from_catalog.py --limit 15
  python scripts/build_vocab_from_catalog.py --no-merge
  python scripts/build_vocab_from_catalog.py --use-cache
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oshisha.auth import OshishaAuth, OshishaAuthError  # noqa: E402
from oshisha.vocab_builder import (  # noqa: E402
    GENERATED_DIR,
    VOCAB_DIR,
    VocabBuilder,
    write_vocab_files,
)

DEFAULT_ROOT = "/catalog/kalyannye_smesi/"


def main() -> int:
    load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(description="Автогенерация vocab из каталога Oshisha")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Корневой раздел каталога")
    parser.add_argument("--limit", type=int, default=0, help="Макс. число брендов (0 = все)")
    parser.add_argument("--delay", type=float, default=0.35, help="Пауза между запросами (сек)")
    parser.add_argument("--no-cache", action="store_true", help="Не читать/писать кэш")
    parser.add_argument("--use-cache", action="store_true", help="Только из кэша (без запросов)")
    parser.add_argument("--login", action="store_true", help="Принудительный вход")
    args = parser.parse_args()

    email = os.environ.get("OSHISHA_EMAIL")
    password = os.environ.get("OSHISHA_PASSWORD")
    base_url = os.environ.get("OSHISHA_BASE_URL", "https://oshisha.cc")
    session_path = ROOT / "data" / "sessions" / "oshisha.json"

    with OshishaAuth(base_url, session_file=session_path) as auth:
        if args.login or not auth.is_authenticated:
            if not email or not password:
                print("Задайте OSHISHA_EMAIL и OSHISHA_PASSWORD в .env")
                return 1
            try:
                auth.login_email(email, password)
                print("Вход выполнен.")
            except OshishaAuthError as exc:
                print(f"Ошибка входа: {exc}")
                return 1

        builder = VocabBuilder(auth, delay_sec=0 if args.use_cache else args.delay)

        if args.use_cache:
            slugs = sorted(builder.load_cache().keys())
            print(f"Разделов в кэше: {len(slugs)}")
        else:
            print(f"Сканируем разделы из {args.root}...")
            slugs = builder.discover_sections(args.root)
            print(f"Найдено разделов-брендов: {len(slugs)}")

        if args.limit > 0:
            slugs = slugs[: args.limit]
            print(f"Ограничение: {args.limit} разделов")

        if not args.use_cache:
            builder.scan_all(slugs, use_cache=not args.no_cache)
        else:
            cache = builder.load_cache()
            for slug in slugs:
                if slug in cache:
                    builder.scan_section(slug, cache[slug])

        brands = builder.to_brands_json()
        flavors = builder.to_flavors_json()

    write_vocab_files(brands, flavors)

    print()
    print(f"Брендов: {len(brands)}")
    print(f"Вкусов: {len(flavors)}")
    print(f"Файлы: {GENERATED_DIR / 'brands.json'}")
    print(f"       {GENERATED_DIR / 'flavors.json'}")
    print(f"Ручные дополнения (не перезаписываются): {VOCAB_DIR / 'brands.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
