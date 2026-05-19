#!/usr/bin/env python3
"""Проверка сохранённой сессии Oshisha."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from oshisha import OshishaAuth  # noqa: E402


def main() -> int:
    session_path = ROOT / "data" / "sessions" / "oshisha.json"
    if not session_path.exists():
        print(f"Сессия не найдена: {session_path}")
        print("Сначала выполните: python scripts/login_oshisha.py")
        return 1

    with OshishaAuth(session_file=session_path) as auth:
        resp = auth.get("/personal/")
        logged_in = "Выйти" in resp.text or auth.is_authenticated
        print(f"authToken: {'да' if auth.auth_token else 'нет'}")
        print(f"Личный кабинет доступен: {'да' if logged_in else 'нет'}")
        return 0 if logged_in else 1


if __name__ == "__main__":
    raise SystemExit(main())
