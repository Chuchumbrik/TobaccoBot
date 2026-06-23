"""Логирование пользовательских запросов и ответов бота.

Каждый запрос и ответ записываются в data/user_logs/YYYY-MM-DD.jsonl.
Один файл на день — удобно просматривать, фильтровать через jq, анализировать скриптом.

Формат записей (JSONL):
    # Запрос пользователя
    {"ts":"2026-05-26T14:32:15","user_id":123,"username":"vasya","type":"Q",
     "mode":"idle","intent":"flavor_search","text":"малина лимон 200"}

    # Ответ бота (следует сразу после Q)
    {"ts":"2026-05-26T14:32:17","user_id":123,"username":"vasya","type":"A",
     "intent":"flavor_search","preview":"Малина Лимон: найдено 5 позиций\n• Al Fakher…"}

Значения type:
    Q  — запрос пользователя
    A  — ответ бота

Значения intent (определяется в routing.py):
    flavor_search   — поиск по вкусу (idle или MODE_FLAVOR)
    advise          — советник по вкусам (idle или MODE_ADVISE)
    mix             — подбор миксов
    refine          — уточнение предыдущего результата советника/миксов
    advise_clarify  — ответ на уточняющий вопрос советника
    theme           — тематический поиск
    chat            — свободный чат
    ack             — нейтральное подтверждение (спасибо, ок и т.п.)
    check           — проверка одной/нескольких позиций
    cart_add        — добавление в корзину (ввод позиций)
    command         — /команды (start, help, search с аргументом и т.д.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from telegram import Update

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "data" / "user_logs"

# ── Хранилище последнего intent по user_id ───────────────────────────────────
# Один пользователь = один активный запрос в любой момент.
# Используется в log_response() чтобы связать ответ с запросом.
_pending_intents: dict[int, str] = {}

# HTML-теги для очистки preview
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_RESPONSE_PREVIEW_LEN = 400  # символов в preview ответа


def _log_path() -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    return LOG_DIR / f"{today}.jsonl"


def _write_entry(entry: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("query_logger: не удалось записать: %s", exc)


def _strip_html(text: str) -> str:
    """Убрать HTML-теги, схлопнуть пробелы, обрезать до лимита."""
    clean = _HTML_TAG_RE.sub("", text).strip()
    # Схлопываем множественные пустые строки
    clean = re.sub(r"\n{3,}", "\n\n", clean)
    if len(clean) > _RESPONSE_PREVIEW_LEN:
        clean = clean[:_RESPONSE_PREVIEW_LEN] + "…"
    return clean


async def log_query(
    update: Update,
    *,
    text: str,
    intent: str,
    mode: str = "idle",
) -> None:
    """Записать запрос пользователя (type=Q).

    Также сохраняет intent в памяти — log_response() подтянет его автоматически.

    Args:
        update:  Telegram Update с информацией о пользователе.
        text:    Текст запроса (как написал пользователь).
        intent:  Тип запроса (flavor_search, advise, mix, …).
        mode:    Режим бота при вводе (idle, MODE_FLAVOR и т.д.).
    """
    user = update.effective_user
    if user and user.id:
        _pending_intents[user.id] = intent

    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "user_id": user.id if user else None,
        "username": user.username if user else None,
        "type": "Q",
        "mode": mode,
        "intent": intent,
        "text": text,
    }
    await asyncio.to_thread(_write_entry, entry)


async def log_response(
    update: Update,
    *,
    text: str,
) -> None:
    """Записать ответ бота (type=A).

    Intent подтягивается из _pending_intents по user_id — сохранён в log_query().
    Если запрос не был залогирован (системные сообщения, /start и т.п.),
    intent будет None — запись всё равно сохраняется.

    Args:
        update:  Telegram Update текущего запроса.
        text:    Текст ответа бота (HTML допустим — будет очищен для preview).
    """
    user = update.effective_user
    user_id = user.id if user else None
    intent = _pending_intents.pop(user_id, None) if user_id else None

    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "user_id": user_id,
        "username": user.username if user else None,
        "type": "A",
        "intent": intent,
        "preview": _strip_html(text),
    }
    await asyncio.to_thread(_write_entry, entry)
