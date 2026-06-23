"""Чат-режим: вопросы о табаках и кальянах, вспомогательный."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.handlers.common import get_config, user_snapshot
from bot.inline_keyboards import chat_action_keyboard
from bot.llm_gate import (
    begin_request_llm_budget,
    check_llm_allowed,
    consume_request_llm_budget,
    end_request_llm_budget,
)
from oshisha.llm import chat_about_hookah, start_llm_trace

logger = logging.getLogger(__name__)


# ── Детектор chat-запроса ────────────────────────────────────────────────────

_CHAT_QUESTION_WORDS: tuple[str, ...] = (
    "как ", "как?",
    "почему", "зачем", "когда ",
    "что такое", "что это ", "что это?",
    "чем отличается", "чем отличаются", "в чём разница", "в чем разница",
    "расскажи", "объясни", "поясни",
    "что лучше", "какой лучше", "какая лучше", "что лучше?",
    "можно ли", "стоит ли", "нужно ли",
    "сколько ", "как часто", "когда менять",
    "что значит", "что делать",
    "как выбрать", "как понять", "как определить",
    "чем отмыть", "чем чистить",
)

_CHAT_HOOKAH_TOPICS: tuple[str, ...] = (
    "уголь", "угли",
    "чаша", "чашу", "чашей",
    "забивка", "забивать", "набивка", "набить",
    "продув", "продуть",
    "калауд", "калаудом",
    "фольга", "фольгой",
    "колба", "шланг", "кальян ",
    "крепость табака", "крепкий табак", "лёгкий табак",
    "линейка ", "серия ", "новинка",
    "жар ", "тяга ", "дым ",
    "слой табака", "сколько табака",
)


def _looks_like_chat(text: str) -> bool:
    """True если текст — информационный вопрос о табаках/кальянах, а не поиск товара."""
    t = text.lower().strip()
    # Явный вопрос
    if t.endswith("?"):
        return True
    # Вопросительные слова в начале или в середине
    if any(t.startswith(w) or f" {w}" in t for w in _CHAT_QUESTION_WORDS):
        return True
    # Упоминание hookah-темы с достаточным контекстом (не просто слово)
    has_topic = any(w in t for w in _CHAT_HOOKAH_TOPICS)
    if has_topic and len(t.split()) >= 4:
        return True
    return False


# ── Хендлер ─────────────────────────────────────────────────────────────────

async def _run_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    """Отвечает на вопрос о табаках/кальянах через LLM."""
    if not update.message:
        return

    config = get_config(context)
    user_id, _, _ = user_snapshot(update)

    if not check_llm_allowed(context, config, user_id, calls=1):
        await update.message.reply_text(
            "Слишком много запросов к ИИ за час. Попробуйте позже."
        )
        return

    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)
    try:
        if not consume_request_llm_budget(context, 1):
            await update.message.reply_text("Превышен лимит запросов.")
            return

        result = await chat_about_hookah(text)
        answer = result.get("answer") or "Не могу ответить на этот вопрос."
        action = result.get("action")

        markup = chat_action_keyboard(action)
        await update.message.reply_text(answer, reply_markup=markup)

        logger.info(
            "chat: user=%d q=%r action=%s",
            user_id, text[:60], action,
        )
    except Exception:
        logger.exception("_run_chat failed for %r", text)
        await update.message.reply_text("Ошибка. Попробуй ещё раз.")
    finally:
        end_request_llm_budget()
