"""Меню, режимы ввода, свободный текст."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.action_context import (
    clear_action_context,
    clear_advise_context,
    get_advise_description,
    get_clarify_state,
    get_was_mix,
    save_clarify_state,
)
from bot.handlers.common import (
    get_config,
    get_menu_button_set,
    is_compare_enabled,
    prompt_flavor,
    prompt_list,
    prompt_single,
    reply,
    send_help,
    welcome_text,
)
from bot.handlers.compare import prompt_compare
from bot.menu_state import clear_mode
from bot.keyboards import (
    BTN_ADVISE,
    BTN_CANCEL,
    BTN_CART_LOG,
    BTN_CHECK,
    BTN_CHECK_LIST,
    BTN_COMPARE,
    BTN_HELP,
    BTN_LOG_RESET,
    BTN_MENU,
    BTN_SEARCH,
    BTN_VIEW_CART,
)
from bot.menu_state import (
    MODE_ADVISE,
    MODE_ADVISE_CLARIFY,
    MODE_ADVISE_REFINE,
    MODE_CART_LIST,
    MODE_CART_SINGLE,
    MODE_COMPARE,
    MODE_FLAVOR,
    MODE_LIST,
    MODE_SINGLE,
    PROMPT_FOOTER,
    get_mode,
    has_mode,
    set_mode,
)
from bot.handlers.advise import (
    _is_fresh_advise_request,
    _looks_like_advise,
    _looks_like_mix,
    _run_advise,
    _run_advise_after_clarify,
    _run_advise_refine,
)
from bot.handlers.mix_flow import _run_advise_mix
from bot.handlers.chat import _looks_like_chat, _run_chat
from bot.handlers.theme import _looks_like_theme, _run_theme_search
from bot.handlers.cart_flow import (
    _run_cart_add,
    _run_cart_log,
    _run_cart_view,
    _run_log_reset,
)
from bot.handlers.check_flow import _run_list_check, _run_single_check
from bot.handlers.compare import handle_compare_input
from bot.handlers.flavor import _run_flavor_search
from bot.inline_keyboards import clarify_question_keyboard, welcome_keyboard
from bot.query_logger import log_query

logger = logging.getLogger(__name__)


# ── Парсинг структурированного списка ────────────────────────────────────────

_SEPARATOR_RE = re.compile(r"^[\s\-_—–=*\./|]+$")
_CATEGORY_WORDS = frozenset(["табак", "кальян", "кальянный", "hookah", "tobacco"])


def _parse_brand_structured_list(text: str) -> list[str]:
    """Разбирает многострочный список, поддерживая заголовки брендов.

    Строка вида «Бренд:» запоминается как контекст бренда — все следующие
    строки получают этот бренд как префикс запроса («Бренд аромат»).
    Строки-сепараторы (тире/прочерки/подчёркивания) и одиночные
    категорийные слова («Табак», «Кальян») фильтруются.
    """
    out: list[str] = []
    current_brand: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _SEPARATOR_RE.fullmatch(line):
            continue
        if line.lower() in _CATEGORY_WORDS:
            continue
        if line.endswith(":"):
            brand = line[:-1].strip()
            if brand:
                current_brand = brand
            continue
        if current_brand:
            out.append(f"{current_brand} {line}")
        else:
            out.append(line)

    return out


_NL_STARTERS: frozenset[str] = frozenset([
    "хочу", "ищу", "найди", "нет", "без", "не", "что", "как", "подбери",
    "посоветуй", "можешь", "хочется", "нравится", "дай", "покажи",
    "suggest", "want", "give", "show", "help",
])


def _looks_like_check_list(text: str) -> bool:
    """True если текст — список позиций для проверки наличия.

    Два варианта:
    1. Есть заголовки брендов («Бренд:») → достаточно 2 строк.
    2. Обычный список ≥4 коротких строк без слов-маркеров естественного языка.
    """
    if "\n" not in text:
        return False

    # Вариант 1: заголовки брендов («Бренд:» на отдельной строке)
    has_brand_header = any(
        ln.strip().endswith(":")
        and not _SEPARATOR_RE.fullmatch(ln.strip())
        and ln.strip()[:-1].strip()
        and ln.strip()[:-1].strip().lower() not in _CATEGORY_WORDS
        for ln in text.splitlines()
    )
    if has_brand_header:
        return len(_parse_brand_structured_list(text)) >= 2

    # Вариант 2: обычный список — минимум 4 непустых коротких строки
    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip()
        and not _SEPARATOR_RE.fullmatch(ln.strip())
        and ln.strip().lower() not in _CATEGORY_WORDS
    ]
    if len(lines) < 4:
        return False
    for ln in lines:
        if len(ln) > 80:
            return False
        first_word = ln.lower().split()[0].rstrip("!?,") if ln.split() else ""
        if first_word in _NL_STARTERS:
            return False
    return True


# ── Детектор приветствий ─────────────────────────────────────────────────────

_GREETING_WORDS: frozenset[str] = frozenset([
    "привет", "приветик", "здравствуй", "здравствуйте",
    "здарова", "здорово", "приём", "прием",
    "хай", "hi", "hello", "хелло",
    "добрый день", "добрый вечер", "доброе утро", "добрый",
    "доброй ночи",
    "кто здесь", "есть кто",
])

_GREETING_RESPONSES = [
    "Привет! 👋 Чем помочь с кальяном?",
    "Привет! Выбирай что делаем 👇",
    "Хай! Готов помочь с выбором табака 😊",
]

_greeting_idx = 0


def _looks_like_greeting(text: str) -> bool:
    """True если текст — приветствие (без примеси поискового запроса)."""
    t = text.lower().strip().rstrip("!., ")
    if t in _GREETING_WORDS:
        return True
    # Многословные приветствия: «добрый день», «есть кто» и т.п.
    for w in _GREETING_WORDS:
        if " " in w and t.startswith(w):
            return True
    return False


# ── Детектор нейтральных подтверждений ──────────────────────────────────────

_ACK_WORDS: frozenset[str] = frozenset([
    "спасибо", "спасиб", "спс", "благодарю", "благодарствую",
    "ок", "окей", "ok", "okay", "хорошо", "отлично", "супер",
    "класс", "круто", "норм", "нормально", "неплохо",
    "понятно", "понял", "поняла", "ясно", "ясненько",
    "ладно", "пойдёт", "пойдет", "принято", "принял",
    "угу", "ага", "ага-ага",
    "готово", "добавил", "добавила", "добавлено", "заказал", "заказала",
    "всё", "все",
    "👍", "👌", "🙏",
])


def _looks_like_ack(text: str) -> bool:
    """True если текст — короткое нейтральное подтверждение (спасибо, ок и т.п.)."""
    t = text.lower().strip().rstrip("!.,")
    # Одно слово-эмодзи или одно слово из словаря
    if t in _ACK_WORDS:
        return True
    # До 3 слов, все слова из словаря (напр. «спасибо большое», «ок понял»)
    words = t.split()
    if 1 <= len(words) <= 3 and all(w.rstrip("!.,") in _ACK_WORDS for w in words):
        return True
    return False


# ── Детектор уточнений предыдущего результата ────────────────────────────────

_REFINE_STARTERS: tuple[str, ...] = (
    # Противопоставление
    "но ", "но,", "но!", "и без ", "и не ",
    # Ограничение
    "только ", "только,",
    # Исключения
    "без ", "кроме ", "исключи", "убери ", "убрать ",
    # Отрицание-модификатор
    "не такое", "не такой", "не такую", "не такая", "не настолько",
    # Степень / компаратив
    "более ", "менее ", "чуть ", "немного ",
    "покрепче", "полегче", "послаще", "покислее", "посвежее",
    "потемнее", "посветлее", "помягче", "пожиже", "погуще",
    "пофруктовее", "поягоднее", "подесертнее",
    "поменьше", "побольше",
    # Альтернативы
    "другой ", "другое ", "другую ", "другие ", "другая ",
    "похожее", "похожий", "похожую", "похожие",
    "другой вариант", "другие варианты",
    "ещё вариант", "ещё варианты",
    # Добавление
    "добавь ", "добавить ", "и с ",
    # Явное уточнение
    "уточни", "уточнить", "измени", "изменить",
    "скорректируй", "поправь",
)


def _looks_like_refine(text: str) -> bool:
    """True если текст — уточнение предыдущего результата советника/миксов.

    Проверяет только лексические маркеры уточнения; вызывается только когда
    в контексте есть сохранённый запрос (get_advise_description непустой).
    """
    t = text.lower().strip()
    return any(t.startswith(s) or t == s.strip() for s in _REFINE_STARTERS)


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text not in get_menu_button_set(context):
        return

    if text == BTN_CANCEL:
        clear_mode(context)
        clear_action_context(context)
        await reply(
            update, context, "Шаг отменён.\n\n" + welcome_text(context),
            reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
        )
        return
    if text == BTN_MENU:
        clear_mode(context)
        clear_action_context(context)
        await reply(
            update, context, welcome_text(context),
            reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
        )
        return
    if text == BTN_HELP:
        clear_mode(context)
        await send_help(update, context)
        return
    if text == BTN_SEARCH:
        await prompt_flavor(update, context)
        return
    if text == BTN_COMPARE:
        await prompt_compare(update, context)
        return
    if text == BTN_ADVISE:
        clear_advise_context(context)
        set_mode(context, MODE_ADVISE)
        await reply(
            update,
            context,
            "🎯 <b>Советник по вкусам</b>\n\n"
            "Опиши что хочешь — ИИ подберёт варианты из каталога.\n\n"
            "<i>Примеры:\n"
            "• хочу сладенькое и ягодное\n"
            "• что-то свежее без мяты\n"
            "• кисло-сладкое, лёгкое</i>"
            + PROMPT_FOOTER,
            parse_mode=ParseMode.HTML,
        )
        return
    if text == BTN_CHECK:
        await prompt_single(update, context)
        return
    if text == BTN_CHECK_LIST:
        await prompt_list(update, context)
        return
    if text == BTN_VIEW_CART:
        clear_mode(context)
        await _run_cart_view(update, context)
        return
    if text == BTN_CART_LOG:
        clear_mode(context)
        await _run_cart_log(update, context)
        return
    if text == BTN_LOG_RESET:
        clear_mode(context)
        await _run_log_reset(update, context)


async def handle_awaiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    if not has_mode(context):
        return

    text = update.message.text.strip()
    if text in get_menu_button_set(context):
        return

    mode = get_mode(context)
    if mode == MODE_COMPARE:
        await handle_compare_input(update, context, text, mode)
        return

    if mode == MODE_FLAVOR:
        if not text:
            await reply(update, context, "Введите вкус, например: <code>малина 200</code>")
            return
        clear_mode(context)
        await log_query(update, text=text, intent="flavor_search", mode=mode)
        await _run_flavor_search(update, context, text)
        return

    if mode == MODE_SINGLE:
        if "\n" in text:
            await reply(
                update,
                context,
                "Нужна <b>одна</b> строка. Для списка — кнопка «📝 Список».",
            )
            return
        clear_mode(context)
        await log_query(update, text=text, intent="check", mode=mode)
        await _run_single_check(update, context, text)
        return

    if mode == MODE_LIST:
        lines = _parse_brand_structured_list(text)
        config = get_config(context)
        if len(lines) < 2:
            await reply(update, context, "Нужно минимум <b>2</b> строки (каждая с новой строки).")
            return
        if len(lines) > config.check_list_max_lines:
            await reply(
                update,
                context,
                f"Слишком много строк (макс. {config.check_list_max_lines}). "
                "Разбейте на части.",
            )
            return
        clear_mode(context)
        await log_query(update, text=text, intent="check", mode=mode)
        await _run_list_check(update, context, lines)
        return

    if mode == MODE_CART_SINGLE:
        if "\n" in text:
            await reply(
                update,
                context,
                "Нужна <b>одна</b> строка. Несколько — команда /cartlist.",
            )
            return
        clear_mode(context)
        await log_query(update, text=text, intent="cart_add", mode=mode)
        await _run_cart_add(update, context, [text])
        return

    if mode == MODE_CART_LIST:
        lines = _parse_brand_structured_list(text)
        config = get_config(context)
        if len(lines) < 2:
            await reply(update, context, "Нужно минимум <b>2</b> строки.")
            return
        if len(lines) > config.check_list_max_lines:
            await reply(
                update,
                context,
                f"Слишком много строк (макс. {config.check_list_max_lines}).",
            )
            return
        clear_mode(context)
        await log_query(update, text=text, intent="cart_add", mode=mode)
        await _run_cart_add(update, context, lines)
        return

    if mode == MODE_ADVISE:
        clear_mode(context)
        if _looks_like_mix(text):
            await log_query(update, text=text, intent="mix", mode=mode)
            await _run_advise_mix(update, context, text)
        else:
            await log_query(update, text=text, intent="advise", mode=mode)
            await _run_advise(update, context, text)
        return

    if mode == MODE_ADVISE_REFINE:
        original = get_advise_description(context)
        clear_mode(context)
        await log_query(update, text=text, intent="refine", mode=mode)
        await _run_advise_refine(update, context, original, text)
        return

    if mode == MODE_ADVISE_CLARIFY:
        original, is_mix = get_clarify_state(context)
        clear_mode(context)

        if _looks_like_mix(text):
            logger.info(
                "Clarify mode: fresh mix request %r detected (original was %r), restarting",
                text, original,
            )
            await log_query(update, text=text, intent="mix", mode=mode)
            await _run_advise_mix(update, context, text)
            return

        if _is_fresh_advise_request(text) and text.lower().strip() != (original or "").lower().strip():
            logger.info(
                "Clarify mode: fresh advise request %r detected (original was %r), restarting",
                text, original,
            )
            await log_query(update, text=text, intent="advise", mode=mode)
            await _run_advise(update, context, text)
            return

        logger.info("Clarify answer: %r + %r", original, text)
        await log_query(update, text=text, intent="advise_clarify", mode=mode)
        if is_mix:
            combined = f"{original}, {text}" if original else text
            await _run_advise_mix(update, context, combined)
        else:
            await _run_advise_after_clarify(update, context, original, text)


async def handle_idle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/") or text in get_menu_button_set(context):
        return

    if _looks_like_greeting(text):
        global _greeting_idx
        msg = _GREETING_RESPONSES[_greeting_idx % len(_GREETING_RESPONSES)]
        _greeting_idx += 1
        await log_query(update, text=text, intent="greeting")
        await reply(
            update,
            context,
            msg,
            reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
        )
        return

    if _looks_like_ack(text):
        await log_query(update, text=text, intent="ack")
        await reply(
            update,
            context,
            "👍 Принято! Чем ещё могу помочь?",
            reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
        )
        return

    # Структурированный список «Бренд:\nАромат» — сразу в проверку наличия
    if _looks_like_check_list(text):
        lines = _parse_brand_structured_list(text)
        config = get_config(context)
        if len(lines) <= config.check_list_max_lines:
            await log_query(update, text=text, intent="check")
            await _run_list_check(update, context, lines)
            return

    # Свежий микс-запрос — всегда в приоритете, независимо от контекста
    if _looks_like_mix(text):
        await log_query(update, text=text, intent="mix")
        await _run_advise_mix(update, context, text)
        return

    # Контекстное уточнение — если есть сохранённый результат советника/миксов
    last_query = get_advise_description(context)
    if last_query and _looks_like_refine(text):
        logger.info(
            "idle_text: contextual refine (was_mix=%s) query=%r refine=%r",
            get_was_mix(context), last_query[:50], text[:60],
        )
        await log_query(update, text=text, intent="refine")
        await _run_advise_refine(update, context, last_query, text)
        return

    if _looks_like_advise(text):
        await log_query(update, text=text, intent="advise")
        await _run_advise(update, context, text)
    elif _looks_like_theme(text):
        await log_query(update, text=text, intent="theme")
        await _run_theme_search(update, context, text)
    elif _looks_like_chat(text):
        await log_query(update, text=text, intent="chat")
        await _run_chat(update, context, text)
    else:
        await log_query(update, text=text, intent="flavor_search")
        await _run_flavor_search(update, context, text)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if has_mode(context):
        await handle_awaiting_input(update, context)
    else:
        await handle_idle_text(update, context)
