"""Проверка позиций и списков."""

from __future__ import annotations


import asyncio
import dataclasses
import logging
import re
import sys
from pathlib import Path
from typing import Literal

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
    CB_CHECK_MIN_WEIGHT,
    CB_CHECK_MAX_WEIGHT,
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
    remove_pick_message as _remove_pick_message,
    reply,
    reply_step,
    send_help,
    send_status,
    show_pick_message as _show_pick_message,
    user_snapshot,
)
from bot import service_async as osh
from oshisha.auth import OshishaAuthError

logger = logging.getLogger(__name__)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    line = " ".join(context.args).strip() if context.args else ""
    if not line:
        await prompt_single(update, context)
        return
    clear_mode(context)
    await _run_single_check(update, context, line)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await prompt_list(update, context)


async def _run_single_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    line: str,
) -> None:
    if not update.message:
        return
    status = await send_status(update, "Проверяю…")
    try:
        results = await osh.check_list(get_service(context), [line])
        save_checks(context, results)
        inline = check_results_keyboard(results)
        await finish_status(
            status,
            update,
            format_check_results(results),
            parse_mode=ParseMode.HTML,
            inline_markup=inline,
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа: {exc}", parse_mode=None)
    except Exception:
        logger.exception("single check failed")
        await finish_status(status, update, "Ошибка при проверке.", parse_mode=None)

async def _run_list_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lines: list[str],
) -> None:
    if not update.message:
        return
    status = await send_status(update, f"Проверяю {len(lines)} позиций…")
    try:
        results = await osh.check_list(get_service(context), lines)
        save_checks(context, results)
        text = format_check_results(results)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            inline_markup=check_results_keyboard(results),
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("check_list failed")
        await finish_status(status, update, "Ошибка при проверке списка.", parse_mode=None)

async def _callback_check_pick(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    try:
        index = int(data.removeprefix(CB_CHECK_PICK))
    except ValueError:
        if update.callback_query:
            await update.callback_query.answer("Некорректная кнопка")
        return

    checks = get_checks(context)
    if index < 0 or index >= len(checks):
        if update.callback_query:
            await update.callback_query.answer(
                "Результаты устарели — повторите проверку", show_alert=True
            )
        return

    check = checks[index]
    if check.status != "есть":
        if update.callback_query:
            await update.callback_query.answer("Нет в наличии", show_alert=True)
        return

    in_stock = _in_stock_check_indices(checks)
    text = format_check_pick_confirm(
        check,
        list_number=index + 1,
        variants_in_stock=len(in_stock),
    )
    await _show_pick_message(
        update,
        context,
        text=text,
        reply_markup=check_confirm_keyboard(index),
    )

async def _callback_check_all(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    if not query:
        return

    checks = get_checks(context)
    indices = _in_stock_check_indices(checks)
    if not indices:
        await query.answer("Нет позиций в наличии", show_alert=True)
        return

    try:
        batch = await osh.add_checks_to_cart(get_service(context), checks, indices=indices)
        log_cart_batch(update, context, batch)
        await query.answer(f"✅ Добавлено: {batch.added_count} из {len(indices)}")
        chat_id = query.message.chat_id if query.message else None
        if chat_id:
            await _remove_pick_message(context, chat_id)
            await context.bot.send_message(
                chat_id,
                format_cart_batch(batch),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=after_cart_keyboard(),
            )
    except OshishaAuthError as exc:
        await query.answer(f"Ошибка входа: {exc}", show_alert=True)
    except Exception:
        logger.exception("callback check add-all cart failed")
        await query.answer("Ошибка добавления", show_alert=True)


async def _callback_check_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        index = int(data.removeprefix(CB_CHECK_CONFIRM))
    except ValueError:
        await query.answer("Некорректная кнопка")
        return

    checks = get_checks(context)
    if index < 0 or index >= len(checks):
        await query.answer("Результаты устарели — повторите проверку", show_alert=True)
        return

    check = checks[index]
    if check.status != "есть":
        await query.answer("Нет в наличии", show_alert=True)
        return

    try:
        batch = await osh.add_checks_to_cart(get_service(context), checks, indices=[index])
        log_cart_batch(update, context, batch)
        item = batch.items[0] if batch.items else None
        if item and item.success:
            await query.answer("✅ Добавлено в корзину")
            chat_id = query.message.chat_id if query.message else None
            if chat_id:
                await _remove_pick_message(context, chat_id)
                await context.bot.send_message(
                    chat_id,
                    format_cart_item(item),
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                    reply_markup=after_cart_keyboard(),
                )
        else:
            msg = item.message if item else "ошибка"
            await query.answer(f"❌ {msg}", show_alert=True)
    except OshishaAuthError as exc:
        await query.answer(f"Ошибка входа: {exc}", show_alert=True)
    except Exception:
        logger.exception("callback check cart failed")
        await query.answer("Ошибка добавления", show_alert=True)


def _pick_by_weight(
    checks: list,
    mode: Literal["min", "max"],
) -> list:
    """Для каждого найденного чека выбрать нужную граммовку по вариантам."""
    from oshisha.catalog import ProductCheckResult
    result = []
    for check in checks:
        if check.status == "не найден":
            continue
        in_stock_variants = [v for v in (check.weight_variants or []) if v.in_stock]
        if not in_stock_variants:
            if check.status == "есть":
                result.append(check)
            continue
        target = (
            min(in_stock_variants, key=lambda v: v.weight_g or 0)
            if mode == "min"
            else max(in_stock_variants, key=lambda v: v.weight_g or 0)
        )
        result.append(dataclasses.replace(
            check,
            product_id=target.product_id,
            status="есть",
            url=target.url,
            price=target.price,
            max_quantity=target.max_quantity,
            matched_weight_g=target.weight_g,
        ))
    return result


async def _callback_check_by_weight(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    mode: Literal["min", "max"],
) -> None:
    query = update.callback_query
    if not query:
        return
    checks = get_checks(context)
    selected = _pick_by_weight(checks, mode)
    if not selected:
        await query.answer("Нет позиций в наличии", show_alert=True)
        return
    label = "мин." if mode == "min" else "макс."
    try:
        # Передаём selected напрямую (product_id уже переопределён на нужную граммовку)
        batch = await osh.add_checks_to_cart(get_service(context), selected)
        log_cart_batch(update, context, batch)
        await query.answer(f"✅ Добавлено по {label} весу: {batch.added_count}")
        chat_id = query.message.chat_id if query.message else None
        if chat_id:
            await context.bot.send_message(
                chat_id,
                format_cart_batch(batch),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=after_cart_keyboard(),
            )
    except OshishaAuthError as exc:
        await query.answer(f"Ошибка входа: {exc}", show_alert=True)
    except Exception:
        logger.exception("callback check by weight failed")
        await query.answer("Ошибка добавления", show_alert=True)
