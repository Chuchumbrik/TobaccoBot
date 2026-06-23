"""Сравнение поиска/списка на нескольких сайтах (/compare)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import service_async as osh
from bot.handlers.common import (
    finish_status,
    get_config,
    get_shop_hub,
    is_compare_enabled,
    reply,
    reply_step,
    send_status,
)
from bot.menu_state import MODE_COMPARE, MODE_COMPARE_LIST, clear_mode, set_mode
from oshisha.auth import OshishaAuthError
from shops.format_compare import format_compare_list, format_compare_search

logger = logging.getLogger(__name__)

PROMPT_COMPARE = (
    "⚖️ <b>Сравнение магазинов</b>\n\n"
    "Одна строка — поиск по вкусу на всех сайтах:\n"
    "• <code>малина 200</code>\n\n"
    "Несколько строк (мин. 2) — проверка списка на каждом сайте.\n\n"
    "Или: <code>/compare малина 200</code>"
    "\n\n<i>❌ под сообщением — отмена</i>"
)


async def cmd_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_compare_enabled(context):
        return
    if not update.message:
        return

    args = context.args or []
    if not args:
        set_mode(context, MODE_COMPARE)
        await reply_step(update, context, PROMPT_COMPARE)
        return

    sub = args[0].lower()
    if sub == "list" and len(args) > 1:
        clear_mode(context)
        lines = [" ".join(args[1:])]
        await _run_compare_list(update, context, lines)
        return

    query = " ".join(args).strip()
    if not query:
        set_mode(context, MODE_COMPARE)
        await reply_step(update, context, PROMPT_COMPARE)
        return

    clear_mode(context)
    if "\n" in query:
        lines = [ln.strip() for ln in query.splitlines() if ln.strip()]
        await _run_compare_list(update, context, lines)
    else:
        await _run_compare_search(update, context, query)


async def prompt_compare(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_compare_enabled(context):
        return
    set_mode(context, MODE_COMPARE)
    await reply_step(update, context, PROMPT_COMPARE)


async def _run_compare_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    if not update.message:
        return
    config = get_config(context)
    hub = get_shop_hub(context)
    sites = ", ".join(name for _, name in hub.list_sites())
    status = await send_status(update, f"⚖️ Сравниваю «{query}»…\n<i>{sites}</i>")

    try:
        compare = await osh.compare_search_flavor(
            hub,
            query,
            limit=config.flavor_search_limit,
        )
        text = format_compare_search(compare)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа: {exc}", parse_mode=None)
    except Exception:
        logger.exception("compare search failed for %r", query)
        await finish_status(
            status,
            update,
            "Ошибка при сравнении. Попробуйте позже.",
            parse_mode=None,
        )


async def _run_compare_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lines: list[str],
) -> None:
    if not update.message:
        return
    config = get_config(context)
    hub = get_shop_hub(context)
    status = await send_status(
        update, f"⚖️ Сравниваю {len(lines)} поз. на всех сайтах…"
    )

    try:
        compare = await osh.compare_check_list(hub, lines)
        text = format_compare_list(compare)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа: {exc}", parse_mode=None)
    except Exception:
        logger.exception("compare list failed")
        await finish_status(
            status,
            update,
            "Ошибка при сравнении списка.",
            parse_mode=None,
        )


async def handle_compare_input(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    mode: str,
) -> None:
    """Вызывается из routing при MODE_COMPARE / MODE_COMPARE_LIST."""
    config = get_config(context)
    if mode == MODE_COMPARE_LIST or "\n" in text:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            await reply(update, context, "Для сравнения списка нужно минимум <b>2</b> строки.")
            return
        if len(lines) > config.check_list_max_lines:
            await reply(
                update,
                context,
                f"Слишком много строк (макс. {config.check_list_max_lines}).",
            )
            return
        clear_mode(context)
        await _run_compare_list(update, context, lines)
        return

    if not text:
        await reply(update, context, "Введите запрос, например: <code>малина 200</code>")
        return
    clear_mode(context)
    await _run_compare_search(update, context, text)
