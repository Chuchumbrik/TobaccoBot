#!/usr/bin/env python3
"""Запуск Telegram-бота."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bot.app import run_polling  # noqa: E402

if __name__ == "__main__":
    run_polling()
