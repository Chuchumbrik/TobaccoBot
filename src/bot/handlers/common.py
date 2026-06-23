"""Общие утилиты и UI-хелперы для обработчиков Telegram."""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardMarkup, Message, ReplyKeyboardRemove, Update
from bot.inline_keyboards import welcome_keyboard
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.action_context import clear_pick_message_id, get_pick_message_id, set_pick_message_id
from bot.cart_log import entries_from_batch, get_cart_log
from bot.config import BotConfig
from bot.inline_keyboards import inline_cancel_keyboard
from bot.keyboards import menu_buttons
from bot.query_logger import log_response
from bot.menu_state import (
    MODE_CART_LIST,
    MODE_CART_SINGLE,
    MODE_FLAVOR,
    MODE_LIST,
    MODE_SINGLE,
    PROMPT_CART_LIST,
    PROMPT_CART_SINGLE,
    PROMPT_FLAVOR,
    PROMPT_LIST,
    PROMPT_SINGLE,
    clear_mode,
    set_mode,
)
from bot.messages import format_help_chunks, format_welcome
from shops.hub import ShopHub

logger = logging.getLogger(__name__)

SERVICE_KEY = "shop_hub"
CONFIG_KEY = "bot_config"
COMPARE_AVAILABLE_KEY = "compare_available"
MENU_BUTTONS_KEY = "menu_buttons"


def get_shop_hub(context: ContextTypes.DEFAULT_TYPE) -> ShopHub:
    hub = context.application.bot_data.get(SERVICE_KEY)
    if hub is None:
        hub = ShopHub.from_env()
        context.application.bot_data[SERVICE_KEY] = hub
    return hub


def get_service(context: ContextTypes.DEFAULT_TYPE) -> ShopHub:
    """Primary-магазин + compare_* (обратная совместимость имени)."""
    return get_shop_hub(context)


def get_config(context: ContextTypes.DEFAULT_TYPE) -> BotConfig:
    return context.application.bot_data[CONFIG_KEY]


def is_compare_enabled(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get(COMPARE_AVAILABLE_KEY))


def get_menu_button_set(context: ContextTypes.DEFAULT_TYPE) -> frozenset[str]:
    return context.application.bot_data.get(MENU_BUTTONS_KEY) or menu_buttons(
        compare=False
    )


def _menu_kw(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return {"compare": is_compare_enabled(context)}


def user_snapshot(update: Update) -> tuple[int, str | None, str | None]:
    user = update.effective_user
    if not user:
        return 0, None, None
    return user.id, user.username, user.full_name


def can_view_all_cart_log(config: BotConfig, user_id: int) -> bool:
    if not config.telegram_admin_ids:
        return True
    return user_id in config.telegram_admin_ids


async def send_status(update: Update, text: str) -> Message | None:
    if not update.message:
        return None
    return await update.message.reply_text(text)


async def send_result_message(
    status: Message | None,
    update: Update,
    text: str,
    *,
    text_kwargs: dict,
    inline_markup: InlineKeyboardMarkup | None,
) -> None:
    send_kwargs = dict(text_kwargs)
    if inline_markup is not None:
        send_kwargs["reply_markup"] = inline_markup
    if status is not None:
        await status.chat.send_message(text, **send_kwargs)
    elif update.message:
        await update.message.reply_text(text, **send_kwargs)


async def finish_status(
    status: Message | None,
    update: Update,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
    disable_web_page_preview: bool = False,
    inline_markup: InlineKeyboardMarkup | None = None,
) -> None:
    text_kwargs: dict = {}
    if parse_mode is not None:
        text_kwargs["parse_mode"] = parse_mode
    if disable_web_page_preview:
        text_kwargs["disable_web_page_preview"] = True

    if status is None:
        await send_result_message(
            status, update, text, text_kwargs=text_kwargs, inline_markup=inline_markup
        )
        asyncio.ensure_future(log_response(update, text=text))
        return

    try:
        await status.edit_text(text, **text_kwargs)
        if inline_markup is not None:
            try:
                await status.edit_reply_markup(reply_markup=inline_markup)
            except BadRequest as exc:
                err = str(exc).lower()
                if "message is not modified" not in err:
                    raise
    except BadRequest as exc:
        err = str(exc).lower()
        if "message is not modified" in err:
            if inline_markup is not None:
                try:
                    await status.edit_reply_markup(reply_markup=inline_markup)
                except BadRequest:
                    pass
            asyncio.ensure_future(log_response(update, text=text))
            return
        if "can't be edited" in err or "message to edit not found" in err:
            await send_result_message(
                status, update, text, text_kwargs=text_kwargs, inline_markup=inline_markup
            )
            asyncio.ensure_future(log_response(update, text=text))
            return
        raise
    asyncio.ensure_future(log_response(update, text=text))


async def reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
    reply_markup=None,
) -> None:
    if not update.message:
        return
    kwargs: dict = {}
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup
    await update.message.reply_text(text, parse_mode=parse_mode, **kwargs)
    asyncio.ensure_future(log_response(update, text=text))


async def reply_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
) -> None:
    if not update.message:
        return
    await update.message.reply_text(
        text,
        parse_mode=parse_mode,
        reply_markup=inline_cancel_keyboard(),
    )


async def send_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    hub = get_shop_hub(context)
    compare_on = is_compare_enabled(context)
    chunks = format_help_chunks(
        config,
        user_id=user_id,
        is_admin_log=can_view_all_cart_log(config, user_id),
        compare_available=compare_on,
        compare_sites=hub.list_sites() if compare_on else None,
    )
    for i, chunk in enumerate(chunks):
        suffix = f"\n\n<i>(справка {i + 1}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        await update.message.reply_text(
            chunk + suffix,
            parse_mode=ParseMode.HTML,
        )


async def prompt_flavor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_FLAVOR)
    await reply_step(update, context, PROMPT_FLAVOR)


async def prompt_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_SINGLE)
    await reply_step(update, context, PROMPT_SINGLE)


async def prompt_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_LIST)
    config = get_config(context)
    extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк за раз.</i>"
    await reply_step(update, context, PROMPT_LIST + extra)


async def prompt_cart_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_CART_SINGLE)
    await reply_step(update, context, PROMPT_CART_SINGLE)


async def prompt_cart_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_CART_LIST)
    config = get_config(context)
    extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк.</i>"
    await reply_step(update, context, PROMPT_CART_LIST + extra)


def log_cart_batch(update: Update, context: ContextTypes.DEFAULT_TYPE, batch) -> None:
    user_id, username, full_name = user_snapshot(update)
    if not user_id:
        return
    config = get_config(context)
    log = get_cart_log(context, config.cart_log_path)
    session_id = log.load_state().session_id
    log.append_entries(
        entries_from_batch(
            batch,
            telegram_user_id=user_id,
            username=username,
            full_name=full_name,
            session_id=session_id,
        )
    )


def welcome_text(context: ContextTypes.DEFAULT_TYPE) -> str:
    hub = get_shop_hub(context)
    return format_welcome(
        compare_available=is_compare_enabled(context),
        compare_sites=hub.list_sites() if is_compare_enabled(context) else None,
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await reply(
        update, context, welcome_text(context),
        reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await reply(
        update, context, welcome_text(context),
        reply_markup=welcome_keyboard(compare=is_compare_enabled(context)),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await send_help(update, context)


# ── Вспомогательные функции для pick-сообщений ───────────────────────────────
# Вынесены сюда чтобы избежать циклических импортов между callbacks/flavor/check

async def remove_pick_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """Удалить предыдущее pick-сообщение (выбор позиции из списка)."""
    pick_id = get_pick_message_id(context)
    if pick_id is None:
        return
    try:
        await context.bot.delete_message(chat_id, pick_id)
    except BadRequest:
        pass
    clear_pick_message_id(context)


async def show_pick_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    reply_markup,
) -> None:
    """Показать pick-сообщение с подтверждением выбора позиции."""
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat_id if query.message else None
    if not chat_id:
        return
    await query.answer()
    await remove_pick_message(context, chat_id)
    msg = await context.bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    set_pick_message_id(context, msg.message_id)
