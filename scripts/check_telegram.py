#!/usr/bin/env python3
"""Проверка доступа к Telegram Bot API (с учётом TELEGRAM_PROXY из .env)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import httpx  # noqa: E402

from bot.config import load_config  # noqa: E402


def main() -> int:
    config = load_config()
    proxy = config.telegram_proxy
    timeout = config.telegram_connect_timeout

    print(f"Прокси: {proxy or '(нет)'}")
    print(f"Таймаут: {timeout} с")
    print("Запрос: GET https://api.telegram.org ...")

    try:
        with httpx.Client(proxy=proxy, timeout=timeout) as client:
            r = client.get("https://api.telegram.org")
        print(f"OK — HTTP {r.status_code}")
        return 0
    except httpx.TimeoutException:
        print(
            "Таймаут: api.telegram.org недоступен.\n"
            "Включите VPN и добавьте в .env, например:\n"
            "  TELEGRAM_PROXY=http://127.0.0.1:7890\n"
            "Порт смотрите в настройках прокси вашего клиента."
        )
        return 1
    except Exception as exc:
        print(f"Ошибка: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
