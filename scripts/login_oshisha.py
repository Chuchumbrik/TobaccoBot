#!/usr/bin/env python3
"""Проверка входа на oshisha.cc."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oshisha import OshishaAuth, OshishaAuthError  # noqa: E402


def main() -> int:
    load_dotenv(ROOT / ".env")

    email = os.environ.get("OSHISHA_EMAIL")
    password = os.environ.get("OSHISHA_PASSWORD")
    base_url = os.environ.get("OSHISHA_BASE_URL", "https://oshisha.cc")

    if not email or not password:
        print("Задайте OSHISHA_EMAIL и OSHISHA_PASSWORD в .env (см. .env.example)")
        return 1

    session_path = ROOT / "data" / "sessions" / "oshisha.json"

    with OshishaAuth(base_url, session_file=session_path) as auth:
        try:
            result = auth.login_email(email, password)
        except OshishaAuthError as exc:
            print(f"Ошибка входа: {exc}")
            return 1

        print("Вход успешен.")
        print(f"  STEP: {result.get('STEP')}")
        print(f"  authToken: {'да' if auth.auth_token else 'нет'}")
        print(f"  Сессия сохранена: {session_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
