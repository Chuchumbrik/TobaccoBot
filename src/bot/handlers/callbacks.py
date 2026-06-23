"""Inline callback-обработчики."""

from __future__ import annotations


import asyncio
import logging
import re
import sys
from pathlib import Path

from telegram import InlineKeyboardMarkup, Message, ReplyKeyboardRemove, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.action_context import (
    clear_action_context,
    clear_advise_context,
    clear_pick_message_id,
    get_advise_description,
    get_checks,
    get_clarify_state,
    get_exclusions,
    get_flavor_hits,
    get_flavor_query,
    get_mix_recipes,
    get_pick_message_id,
    get_theme_search,
    save_advise_description,
    save_clarify_state,
    save_checks,
    save_exclusions,
    save_flavor_search,
    save_mix_recipes,
    set_pick_message_id,
)
from bot.cart_log import format_session_started, get_cart_log
from bot.config import BotConfig
from bot.search_log import log_search
from bot.formatters import (
    format_cart_batch,
    format_cart_item,
    format_cart_log,
    format_check_pick_confirm,
    format_check_results,
    format_flavor_pick_confirm,
    format_flavor_search,
    format_site_cart,
)
from bot.inline_keyboards import (
    ADVISE_PAGE_SIZE,
    CB_ADVISE_PAGE,
    CB_ADVISE_REFINE,
    CB_ADVISE_RESET,
    CB_BACK_CHECK,
    CB_BACK_FLAVOR,
    CB_CANCEL,
    CB_CLARIFY_RESET,
    CB_CHECK_ALL,
    CB_CHECK_CONFIRM,
    CB_CHECK_PICK,
    CB_DISMISS,
    CB_EXCL_RESET,
    CB_FLAVOR_CONFIRM,
    CB_FLAVOR_GROUP,
    CB_FLAVOR_GEN_MIX,
    CB_FLAVOR_SUGGEST,
    CB_FLAVOR_PICK,
    CB_MIX_BUILD,
    CB_SEARCH_AGAIN,
    CB_THEME_PAGE,
    THEME_PAGE_SIZE,
    CB_VIEW_CART,
    CB_WM_ADVISE,
    CB_WM_SEARCH,
    CB_WM_CHECK,
    CB_WM_LIST,
    CB_WM_CART,
    CB_WM_LOG,
    CB_WM_COMPARE,
    _in_stock_check_indices,
    _in_stock_flavor_indices,
    advise_keyboard,
    after_cart_keyboard,
    check_confirm_keyboard,
    check_results_keyboard,
    clarify_question_keyboard,
    flavor_confirm_keyboard,
    flavor_search_keyboard,
    flavor_search_keyboard_with_mix,
    inline_cancel_keyboard,
    mix_results_keyboard,
    theme_keyboard,
    welcome_keyboard,
)
from bot.keyboards import (
    BTN_ADVISE,
    BTN_CANCEL,
    BTN_CART_LOG,
    BTN_LOG_RESET,
    BTN_CHECK,
    BTN_CHECK_LIST,
    BTN_HELP,
    BTN_MENU,
    BTN_SEARCH,
    BTN_VIEW_CART,
    MENU_BUTTONS,
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
    PROMPT_LIST,
    PROMPT_SINGLE,
    clear_mode,
    get_mode,
    has_mode,
    set_mode,
)
from bot.handlers.common import (
    _menu_kw,
    finish_status,
    get_config,
    get_service,
    is_compare_enabled,
    log_cart_batch,
    prompt_flavor,
    remove_pick_message,
    show_pick_message,
    welcome_text,
)
from bot import service_async as osh
from oshisha.auth import OshishaAuthError
from bot.handlers.mix_flow import _callback_mix_build
from bot.handlers.cart_flow import _run_cart_log_from_chat, _run_cart_view_from_chat
from bot.handlers.check_flow import (
    _callback_check_all,
    _callback_check_confirm,
    _callback_check_pick,
)
from bot.handlers.flavor import (
    _callback_flavor_confirm,
    _callback_flavor_gen_mix,
    _callback_flavor_group,
    _callback_flavor_pick,
    _callback_flavor_suggest,
    _prompt_flavor_from_chat,
)

logger = logging.getLogger(__name__)


async def handle_callback_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    chat_id = query.message.chat_id if query.message else None

    if data == CB_CANCEL:
        clear_mode(context)
        clear_action_context(context)
        await query.answer("Отменено")
        if query.message:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except BadRequest:
                pass
        if chat_id:
            await context.bot.send_message(
                chat_id,
                "Шаг отменён.\n\n" + welcome_text(context),
                parse_mode=ParseMode.HTML,
                reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
            )
        return

    if data == CB_DISMISS:
        await query.answer()
        if chat_id:
            await _remove_pick_message(context, chat_id)
        if query.message:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except BadRequest:
                pass
        return

    if data == CB_SEARCH_AGAIN:
        await query.answer()
        if chat_id:
            await _prompt_flavor_from_chat(context, chat_id)
        return

    if data == CB_VIEW_CART:
        await query.answer()
        if chat_id:
            await _run_cart_view_from_chat(context, chat_id)
        return

    if data == CB_EXCL_RESET:
        excluded = get_exclusions(context)
        save_exclusions(context, [])
        await query.answer("🚫 Фильтры сброшены")
        if query.message:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except BadRequest:
                pass
        if chat_id and excluded:
            excl_str = ", ".join(excluded)
            await context.bot.send_message(
                chat_id,
                f"✅ Исключения сброшены: <i>{excl_str}</i>\n\nТеперь поиск без ограничений.",
                parse_mode=ParseMode.HTML,
            )
        return

    if data == CB_CLARIFY_RESET:
        clear_mode(context)
        await query.answer("Уточнение сброшено")
        if query.message:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except BadRequest:
                pass
        if chat_id:
            await context.bot.send_message(
                chat_id,
                "🔄 Уточнение отменено. Введи новый запрос:",
                reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
            )
        return

    if data == CB_ADVISE_REFINE:
        await query.answer()
        description = get_advise_description(context)
        set_mode(context, MODE_ADVISE_REFINE)
        if chat_id:
            await context.bot.send_message(
                chat_id,
                f"✏️ <b>Уточни запрос</b>\n\n"
                f"Исходное: <i>«{description}»</i>\n\n"
                f"Напиши что изменить или добавить:\n"
                f"<i>Примеры: «без мяты», «только BlackBurn», «покислее»</i>",
                parse_mode=ParseMode.HTML,
            )
        return

    if data == CB_ADVISE_RESET:
        clear_advise_context(context)
        set_mode(context, MODE_ADVISE)
        await query.answer("Запрос сброшен")
        if query.message:
            try:
                await query.message.edit_reply_markup(reply_markup=None)
            except BadRequest:
                pass
        if chat_id:
            await context.bot.send_message(
                chat_id,
                "🎯 <b>Советник по вкусам</b>\n\n"
                "Опиши что хочешь — ИИ подберёт варианты из каталога.\n\n"
                "<i>Примеры:\n"
                "• хочу сладенькое и ягодное\n"
                "• что-то свежее без мяты\n"
                "• кисло-сладкое, лёгкое</i>" + PROMPT_FOOTER,
                parse_mode=ParseMode.HTML,
                reply_markup=inline_cancel_keyboard(),
            )
        return

    if data == "sc:noop":
        # Кнопка-заглушка (например «3 / 5» в навигации) — ничего не делает
        await query.answer()
        return

    if data.startswith(CB_ADVISE_PAGE):
        await query.answer()
        try:
            page = int(data[len(CB_ADVISE_PAGE):])
        except ValueError:
            return
        hits = get_flavor_hits(context)
        if not hits or not query.message:
            return

        from bot.formatters import format_hit_groups_lines
        from bot.inline_keyboards import advise_keyboard as _advise_kb
        from bot.weight_groups import group_hits as _grp
        from oshisha import catalog_cache

        groups = _grp(hits)
        total_groups = len(groups)
        in_stock_groups = sum(1 for g in groups if g.status == "есть")
        total_pages = max(1, (total_groups + ADVISE_PAGE_SIZE - 1) // ADVISE_PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))

        description = get_advise_description(context)
        excluded = get_exclusions(context)

        header = f"🎯 <b>Подборка:</b> {description}\n"
        header += f"Найдено: {total_groups} (в наличии: {in_stock_groups})  ·  стр. {page + 1}/{total_pages}\n"
        if excluded:
            header += f"🚫 <i>Исключено: {', '.join(excluded)}</i>\n"
        header += catalog_cache.stock_disclaimer_html() + "\n"

        body_lines = format_hit_groups_lines(hits, page=page, page_size=ADVISE_PAGE_SIZE)
        hint = "\n\n<i>💬 Напиши уточнение прямо в чат — например «без мяты», «только BlackBurn», «покислее»</i>"
        text = header + "\n".join(body_lines) + hint
        if len(text) > 4000:
            text = text[:3990] + "\n…"

        keyboard = _advise_kb(hits, excluded=excluded or None, page=page)
        try:
            await query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        except BadRequest:
            pass
        return

    if data.startswith(CB_THEME_PAGE):
        await query.answer()
        try:
            page = int(data[len(CB_THEME_PAGE):])
        except ValueError:
            return
        theme_data = get_theme_search(context)
        if not theme_data or not query.message:
            await query.answer("Результаты устарели — повтори запрос", show_alert=True)
            return
        from bot.handlers.theme import _format_theme_page
        text = _format_theme_page(
            theme_data["theme"],
            theme_data["theme_display"],
            theme_data["term_hits"],
            page=page,
            page_size=THEME_PAGE_SIZE,
        )
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        keyboard = theme_keyboard(theme_data["term_hits"], page=page)
        try:
            await query.message.edit_text(
                text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        except BadRequest:
            pass
        return

    if data == CB_FLAVOR_GEN_MIX:
        await _callback_flavor_gen_mix(update, context)
        return

    if data == CB_FLAVOR_SUGGEST:
        await _callback_flavor_suggest(update, context)
        return

    if data.startswith(CB_MIX_BUILD):
        await _callback_mix_build(update, context, data)
        return

    if data.startswith(CB_FLAVOR_GROUP):
        await _callback_flavor_group(update, context, data)
        return

    if data.startswith(CB_FLAVOR_PICK):
        await _callback_flavor_pick(update, context, data)
        return

    if data.startswith(CB_FLAVOR_CONFIRM):
        await _callback_flavor_confirm(update, context, data)
        return

    if data == CB_BACK_FLAVOR:
        await _callback_back_to_list(update, context)
        return

    if data == CB_CHECK_ALL:
        await _callback_check_all(update, context)
        return

    if data.startswith(CB_CHECK_PICK):
        await _callback_check_pick(update, context, data)
        return

    if data.startswith(CB_CHECK_CONFIRM):
        await _callback_check_confirm(update, context, data)
        return

    if data == CB_BACK_CHECK:
        await _callback_back_to_list(update, context)
        return

    # ── Кнопки быстрого меню (welcome_keyboard) ───────────────────────────────
    if data == CB_WM_ADVISE:
        await query.answer()
        clear_advise_context(context)
        set_mode(context, MODE_ADVISE)
        if chat_id:
            await context.bot.send_message(
                chat_id,
                "🎯 <b>Советник по вкусам</b>\n\n"
                "Опиши что хочешь — ИИ подберёт варианты из каталога.\n\n"
                "<i>Примеры:\n"
                "• хочу сладенькое и ягодное\n"
                "• что-то свежее без мяты\n"
                "• кисло-сладкое, лёгкое</i>" + PROMPT_FOOTER,
                parse_mode=ParseMode.HTML,
                reply_markup=inline_cancel_keyboard(),
            )
        return

    if data == CB_WM_SEARCH:
        await query.answer()
        if chat_id:
            await _prompt_flavor_from_chat(context, chat_id)
        return

    if data == CB_WM_CHECK:
        await query.answer()
        set_mode(context, MODE_SINGLE)
        if chat_id:
            await context.bot.send_message(
                chat_id,
                PROMPT_SINGLE,
                parse_mode=ParseMode.HTML,
                reply_markup=inline_cancel_keyboard(),
            )
        return

    if data == CB_WM_LIST:
        await query.answer()
        config = get_config(context)
        set_mode(context, MODE_LIST)
        if chat_id:
            extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк за раз.</i>"
            await context.bot.send_message(
                chat_id,
                PROMPT_LIST + extra,
                parse_mode=ParseMode.HTML,
                reply_markup=inline_cancel_keyboard(),
            )
        return

    if data == CB_WM_CART:
        await query.answer()
        if chat_id:
            await _run_cart_view_from_chat(context, chat_id)
        return

    if data == CB_WM_LOG:
        await query.answer()
        if chat_id:
            await _run_cart_log_from_chat(context, chat_id)
        return

    if data == CB_WM_COMPARE:
        await query.answer()
        set_mode(context, MODE_COMPARE)
        if chat_id:
            from bot.handlers.compare import PROMPT_COMPARE
            await context.bot.send_message(
                chat_id,
                PROMPT_COMPARE,
                parse_mode=ParseMode.HTML,
                reply_markup=inline_cancel_keyboard(),
            )
        return

    await query.answer()

# _remove_pick_message и _show_pick_message перенесены в common.py
# для общего доступа из flavor.py и check_flow.py без циклических импортов
_remove_pick_message = remove_pick_message
_show_pick_message = show_pick_message

async def _callback_back_to_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat_id if query.message else None
    await query.answer("Выберите другой вариант в списке выше")
    if chat_id:
        await _remove_pick_message(context, chat_id)
