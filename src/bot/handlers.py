"""Обработчики команд Telegram."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import BotConfig
from bot.formatters import format_check_result, format_flavor_search, format_help
from bot.keyboards import (
    BTN_CANCEL,
    BTN_HELP,
    BTN_LIST,
    BTN_SEARCH,
    BTN_SINGLE,
    MENU_BUTTONS,
    main_menu_keyboard,
)
from bot.menu_state import (
    MODE_FLAVOR,
    MODE_LIST,
    MODE_SINGLE,
    PROMPT_FLAVOR,
    PROMPT_IDLE,
    PROMPT_LIST,
    PROMPT_SINGLE,
    clear_mode,
    get_mode,
    has_mode,
    set_mode,
)
from oshisha.auth import OshishaAuthError
from oshisha.service import OshishaService

logger = logging.getLogger(__name__)

SERVICE_KEY = "oshisha_service"
CONFIG_KEY = "bot_config"


def get_service(context: ContextTypes.DEFAULT_TYPE) -> OshishaService:
    service = context.application.bot_data.get(SERVICE_KEY)
    if service is None:
        service = OshishaService()
        context.application.bot_data[SERVICE_KEY] = service
    return service


def get_config(context: ContextTypes.DEFAULT_TYPE) -> BotConfig:
    return context.application.bot_data[CONFIG_KEY]


async def _reply(
    update: Update,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        text,
        parse_mode=parse_mode,
        reply_markup=main_menu_keyboard(),
    )


async def _prompt_flavor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_FLAVOR)
    await _reply(update, PROMPT_FLAVOR)


async def _prompt_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_SINGLE)
    await _reply(update, PROMPT_SINGLE)


async def _prompt_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_LIST)
    config = get_config(context)
    text = PROMPT_LIST.replace(
        "минимум 2.",
        f"минимум 2, максимум {config.check_list_max_lines}.",
    )
    await _reply(update, text)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _reply(update, format_help())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _reply(update, format_help())


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await _prompt_flavor(update, context)
        return
    clear_mode(context)
    await _run_flavor_search(update, context, query)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ /check [строка] — проверка одной позиции """
    line = " ".join(context.args).strip() if context.args else ""
    if not line:
        await _prompt_single(update, context)
        return
    clear_mode(context)
    await _run_single_check(update, context, line)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ /list — ожидание многострочного списка """
    await _prompt_list(update, context)


async def handle_cyrillic_search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    if not update.message or not update.message.text:
        return
    m = re.match(r"(?i)^/(поиск|vкус)(?:@\w+)?(?:\s+(.*))?$", update.message.text.strip())
    if not m:
        return
    query = (m.group(2) or "").strip()
    if not query:
        await _prompt_flavor(update, context)
        return
    clear_mode(context)
    await _run_flavor_search(update, context, query)


async def handle_menu_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text not in MENU_BUTTONS:
        return

    if text == BTN_CANCEL:
        clear_mode(context)
        await _reply(update, "Отменено. Выберите действие в меню.")
        return
    if text == BTN_HELP:
        clear_mode(context)
        await _reply(update, format_help())
        return
    if text == BTN_SEARCH:
        await _prompt_flavor(update, context)
        return
    if text == BTN_SINGLE:
        await _prompt_single(update, context)
        return
    if text == BTN_LIST:
        await _prompt_list(update, context)


async def handle_awaiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ответ пользователя после выбора режима в меню."""
    if not update.message or not update.message.text:
        return
    if not has_mode(context):
        return

    text = update.message.text.strip()
    if text in MENU_BUTTONS:
        return

    mode = get_mode(context)
    if mode == MODE_FLAVOR:
        if not text:
            await _reply(update, "Введите вкус и граммовку, например: <code>малина 200</code>")
            return
        clear_mode(context)
        await _run_flavor_search(update, context, text)
        return

    if mode == MODE_SINGLE:
        if "\n" in text:
            await _reply(
                update,
                "Нужна <b>одна</b> строка. Лишние переводы строк уберите "
                "или выберите «📝 Список позиций».",
            )
            return
        clear_mode(context)
        await _run_single_check(update, context, text)
        return

    if mode == MODE_LIST:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        config = get_config(context)
        if len(lines) < 2:
            await _reply(
                update,
                "Нужно минимум <b>2</b> строки. Каждая позиция — с новой строки.",
            )
            return
        if len(lines) > config.check_list_max_lines:
            await _reply(
                update,
                f"Слишком много строк (макс. {config.check_list_max_lines}). "
                "Разбейте на несколько сообщений.",
            )
            return
        clear_mode(context)
        await _run_list_check(update, context, lines)


async def handle_idle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Текст без выбранного режима — подсказка, без автопроверки."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/") or text in MENU_BUTTONS:
        return
    await _reply(update, PROMPT_IDLE)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ввод после кнопки или подсказка, если режим не выбран."""
    if has_mode(context):
        await handle_awaiting_input(update, context)
    else:
        await handle_idle_text(update, context)


async def _run_single_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    line: str,
) -> None:
    if not update.message:
        return
    status = await update.message.reply_text(
        "Проверяю…",
        reply_markup=main_menu_keyboard(),
    )
    try:
        results = get_service(context).check_list([line])
        await status.edit_text(
            format_check_result(results[0]),
            parse_mode=ParseMode.HTML,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа: {exc}")
    except Exception:
        logger.exception("single check failed")
        await status.edit_text("Ошибка при проверке.")


async def _run_list_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lines: list[str],
) -> None:
    if not update.message:
        return
    status = await update.message.reply_text(
        f"Проверяю {len(lines)} позиций…",
        reply_markup=main_menu_keyboard(),
    )
    try:
        results = get_service(context).check_list(lines)
        chunks = [format_check_result(r) for r in results]
        text = "\n\n".join(chunks)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await status.edit_text(text, parse_mode=ParseMode.HTML)
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("check_list failed")
        await status.edit_text("Ошибка при проверке списка.")


async def _run_flavor_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    if not update.message:
        return

    config = get_config(context)
    status = await update.message.reply_text(
        f"Ищу «{query}»…",
        reply_markup=main_menu_keyboard(),
    )

    try:
        result = get_service(context).search_flavor(
            query,
            limit=config.flavor_search_limit,
        )
        await status.edit_text(
            format_flavor_search(result),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("flavor search failed for %r", query)
        await status.edit_text("Ошибка при поиске. Попробуйте позже.")
