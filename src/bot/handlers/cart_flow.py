"""Корзина и журнал добавлений."""

from __future__ import annotations


import asyncio
import logging
import re
import sys
from pathlib import Path

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.action_context import (
    clear_action_context,
    clear_pick_message_id,
    get_advise_description,
    get_checks,
    get_clarify_state,
    get_flavor_hits,
    get_flavor_query,
    get_mix_recipes,
    get_pick_message_id,
    save_advise_description,
    save_clarify_state,
    save_checks,
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
    CB_ADVISE_REFINE,
    CB_BACK_CHECK,
    CB_BACK_FLAVOR,
    CB_CANCEL,
    CB_CLARIFY_RESET,
    CB_CHECK_CONFIRM,
    CB_CHECK_PICK,
    CB_DISMISS,
    CB_FLAVOR_CONFIRM,
    CB_FLAVOR_GEN_MIX,
    CB_FLAVOR_PICK,
    CB_MIX_BUILD,
    CB_SEARCH_AGAIN,
    CB_VIEW_CART,
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
    MODE_FLAVOR,
    MODE_LIST,
    MODE_SINGLE,
    PROMPT_FOOTER,
    clear_mode,
    get_mode,
    has_mode,
    set_mode,
)
from bot.messages import format_welcome
from bot.handlers.common import (
    _menu_kw,
    can_view_all_cart_log,
    finish_status,
    get_config,
    get_service,
    log_cart_batch,
    prompt_cart_list,
    prompt_cart_single,
    prompt_flavor,
    prompt_list,
    prompt_single,
    reply,
    reply_step,
    send_help,
    send_status,
    user_snapshot,
)
from bot import service_async as osh
from oshisha.auth import OshishaAuthError

logger = logging.getLogger(__name__)

async def cmd_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    line = " ".join(context.args).strip() if context.args else ""
    if not line:
        await prompt_cart_single(update, context)
        return
    clear_mode(context)
    await _run_cart_add(update, context, [line])

async def cmd_cartlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await prompt_cart_list(update, context)

async def cmd_cartview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _run_cart_view(update, context)

async def cmd_cartlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _run_cart_log(update, context)

async def cmd_logreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _run_log_reset(update, context)

async def _run_cart_add(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lines: list[str],
) -> None:
    if not update.message:
        return
    label = (
        "Добавляю в корзину…"
        if len(lines) == 1
        else f"Добавляю {len(lines)} поз. в корзину…"
    )
    status = await send_status(update, label)
    try:
        batch = await osh.add_to_cart(get_service(context), lines)
        log_cart_batch(update, context, batch)
        text = format_cart_batch(batch)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            inline_markup=after_cart_keyboard(),
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("add_to_cart failed")
        await finish_status(
            status, update, "Ошибка при добавлении в корзину.", parse_mode=None
        )

async def _run_cart_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    status = await send_status(update, "Загружаю корзину с сайта…")
    try:
        cart = await osh.view_cart(get_service(context))
        await finish_status(
            status,
            update,
            format_site_cart(cart),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("view_cart failed")
        await finish_status(status, update, "Не удалось загрузить корзину.", parse_mode=None)

async def _run_cart_view_from_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """Просмотр корзины из callback-кнопки (без update)."""
    status = await context.bot.send_message(chat_id, "Загружаю корзину с сайта…")
    try:
        cart = await osh.view_cart(get_service(context))
        await status.edit_text(
            format_site_cart(cart),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("view_cart from callback failed")
        await status.edit_text("Не удалось загрузить корзину.")

async def _run_cart_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    show_all = can_view_all_cart_log(config, user_id)
    log = get_cart_log(context, config.cart_log_path)
    state = log.load_state()
    if show_all:
        entries = log.read_session(
            state.session_id,
            limit=config.cart_log_display_limit,
        )
        title = "Журнал текущего заказа"
    else:
        entries = log.read_session(
            state.session_id,
            limit=config.cart_log_display_limit,
            telegram_user_id=user_id,
        )
        title = "Ваши добавления (текущий заказ)"
    text = format_cart_log(
        entries,
        title=title,
        show_user=show_all,
        state=state,
    )
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def _run_cart_log_from_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """Показать журнал корзины из callback (без update.message)."""
    config = get_config(context)
    show_all = can_view_all_cart_log(config, chat_id)
    log = get_cart_log(context, config.cart_log_path)
    state = log.load_state()
    if show_all:
        entries = log.read_session(state.session_id, limit=config.cart_log_display_limit)
        title = "Журнал текущего заказа"
    else:
        entries = log.read_session(
            state.session_id,
            limit=config.cart_log_display_limit,
            telegram_user_id=chat_id,
        )
        title = "Ваши добавления (текущий заказ)"
    text = format_cart_log(entries, title=title, show_user=show_all, state=state)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await context.bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _run_log_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, username, full_name = user_snapshot(update)
    if not can_view_all_cart_log(config, user_id):
        await reply(
            update,
            context,
            "Начать новый заказ в журнале могут только администраторы бота "
            "(см. <code>TELEGRAM_ADMIN_IDS</code> в .env).",
        )
        return

    log = get_cart_log(context, config.cart_log_path)
    prev = log.load_state()
    prev_count = len(log.read_session(prev.session_id, limit=10_000))
    state = log.start_new_session(
        user_id=user_id,
        username=username,
        full_name=full_name,
    )
    who = f"@{username}" if username else (full_name or str(user_id))
    await reply(
        update,
        context,
        f"🔄 <b>Новый заказ №{state.session_id}</b>\n\n"
        f"Предыдущий заказ №{prev.session_id} закрыт "
        f"({prev_count} записей в журнале).\n"
        f"Сейчас: <b>{format_session_started(state)}</b> (МСК)\n"
        f"Инициатор: {who}\n\n"
        "Новые добавления в корзину попадут в этот заказ. "
        "Смотреть — «📜 Журнал».",
    )
