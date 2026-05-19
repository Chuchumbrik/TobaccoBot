"""Обработчики команд Telegram."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.cart_log import entries_from_batch, get_cart_log
from bot.config import BotConfig
from bot.formatters import (
    format_cart_batch,
    format_cart_log,
    format_check_results,
    format_flavor_search,
    format_site_cart,
)
from bot.keyboards import (
    BTN_CANCEL,
    BTN_CART,
    BTN_CART_LIST,
    BTN_CART_LOG,
    BTN_CHECK,
    BTN_CHECK_LIST,
    BTN_HELP,
    BTN_MENU,
    BTN_SEARCH,
    BTN_VIEW_CART,
    MENU_BUTTONS,
    main_menu_keyboard,
)
from bot.menu_state import (
    MODE_CART_LIST,
    MODE_CART_SINGLE,
    MODE_FLAVOR,
    MODE_LIST,
    MODE_SINGLE,
    PROMPT_CART_LIST,
    PROMPT_CART_SINGLE,
    PROMPT_FLAVOR,
    PROMPT_IDLE,
    PROMPT_LIST,
    PROMPT_SINGLE,
    clear_mode,
    get_mode,
    has_mode,
    set_mode,
)
from bot.messages import format_help_chunks, format_welcome
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


def _user_snapshot(update: Update) -> tuple[int, str | None, str | None]:
    user = update.effective_user
    if not user:
        return 0, None, None
    return user.id, user.username, user.full_name


def _can_view_all_cart_log(config: BotConfig, user_id: int) -> bool:
    if not config.telegram_admin_ids:
        return True
    return user_id in config.telegram_admin_ids


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


async def _send_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = _user_snapshot(update)
    chunks = format_help_chunks(
        config,
        user_id=user_id,
        is_admin_log=_can_view_all_cart_log(config, user_id),
    )
    for i, chunk in enumerate(chunks):
        suffix = f"\n\n<i>(справка {i + 1}/{len(chunks)})</i>" if len(chunks) > 1 else ""
        await update.message.reply_text(
            chunk + suffix,
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu_keyboard() if i == len(chunks) - 1 else None,
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
    extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк за раз.</i>"
    await _reply(update, PROMPT_LIST + extra)


async def _prompt_cart_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_CART_SINGLE)
    await _reply(update, PROMPT_CART_SINGLE)


async def _prompt_cart_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_CART_LIST)
    config = get_config(context)
    extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк.</i>"
    await _reply(update, PROMPT_CART_LIST + extra)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _reply(update, format_welcome())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _reply(update, format_welcome())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _send_help(update, context)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await _prompt_flavor(update, context)
        return
    clear_mode(context)
    await _run_flavor_search(update, context, query)


async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    line = " ".join(context.args).strip() if context.args else ""
    if not line:
        await _prompt_single(update, context)
        return
    clear_mode(context)
    await _run_single_check(update, context, line)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _prompt_list(update, context)


async def cmd_cart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    line = " ".join(context.args).strip() if context.args else ""
    if not line:
        await _prompt_cart_single(update, context)
        return
    clear_mode(context)
    await _run_cart_add(update, context, [line])


async def cmd_cartlist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _prompt_cart_list(update, context)


async def cmd_cartview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _run_cart_view(update, context)


async def cmd_cartlog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _run_cart_log(update, context)


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
        await _reply(update, "Шаг отменён.\n\n" + format_welcome())
        return
    if text == BTN_MENU:
        clear_mode(context)
        await _reply(update, format_welcome())
        return
    if text == BTN_HELP:
        clear_mode(context)
        await _send_help(update, context)
        return
    if text == BTN_SEARCH:
        await _prompt_flavor(update, context)
        return
    if text == BTN_CHECK:
        await _prompt_single(update, context)
        return
    if text == BTN_CHECK_LIST:
        await _prompt_list(update, context)
        return
    if text == BTN_CART:
        await _prompt_cart_single(update, context)
        return
    if text == BTN_CART_LIST:
        await _prompt_cart_list(update, context)
        return
    if text == BTN_VIEW_CART:
        clear_mode(context)
        await _run_cart_view(update, context)
        return
    if text == BTN_CART_LOG:
        clear_mode(context)
        await _run_cart_log(update, context)


async def handle_awaiting_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            await _reply(update, "Введите вкус, например: <code>малина 200</code>")
            return
        clear_mode(context)
        await _run_flavor_search(update, context, text)
        return

    if mode == MODE_SINGLE:
        if "\n" in text:
            await _reply(
                update,
                "Нужна <b>одна</b> строка. Для списка — кнопка «📝 Список».",
            )
            return
        clear_mode(context)
        await _run_single_check(update, context, text)
        return

    if mode == MODE_LIST:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        config = get_config(context)
        if len(lines) < 2:
            await _reply(update, "Нужно минимум <b>2</b> строки (каждая с новой строки).")
            return
        if len(lines) > config.check_list_max_lines:
            await _reply(
                update,
                f"Слишком много строк (макс. {config.check_list_max_lines}). "
                "Разбейте на части.",
            )
            return
        clear_mode(context)
        await _run_list_check(update, context, lines)
        return

    if mode == MODE_CART_SINGLE:
        if "\n" in text:
            await _reply(
                update,
                "Одна строка — «🛒 Добавить». Несколько — «🛒 Список в корзину».",
            )
            return
        clear_mode(context)
        await _run_cart_add(update, context, [text])
        return

    if mode == MODE_CART_LIST:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        config = get_config(context)
        if len(lines) < 2:
            await _reply(update, "Нужно минимум <b>2</b> строки.")
            return
        if len(lines) > config.check_list_max_lines:
            await _reply(
                update,
                f"Слишком много строк (макс. {config.check_list_max_lines}).",
            )
            return
        clear_mode(context)
        await _run_cart_add(update, context, lines)


async def handle_idle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if text.startswith("/") or text in MENU_BUTTONS:
        return
    await _reply(update, PROMPT_IDLE)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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
            format_check_results(results),
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
        text = format_check_results(results)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await status.edit_text(text, parse_mode=ParseMode.HTML)
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("check_list failed")
        await status.edit_text("Ошибка при проверке списка.")


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
    status = await update.message.reply_text(
        label,
        reply_markup=main_menu_keyboard(),
    )
    try:
        batch = get_service(context).add_to_cart(lines)
        user_id, username, full_name = _user_snapshot(update)
        if user_id:
            config = get_config(context)
            log = get_cart_log(context, config.cart_log_path)
            log.append_entries(
                entries_from_batch(
                    batch,
                    telegram_user_id=user_id,
                    username=username,
                    full_name=full_name,
                )
            )
        text = format_cart_batch(batch)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await status.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("add_to_cart failed")
        await status.edit_text("Ошибка при добавлении в корзину.")


async def _run_cart_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    status = await update.message.reply_text(
        "Загружаю корзину с сайта…",
        reply_markup=main_menu_keyboard(),
    )
    try:
        cart = get_service(context).view_cart()
        await status.edit_text(
            format_site_cart(cart),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("view_cart failed")
        await status.edit_text("Не удалось загрузить корзину.")


async def _run_cart_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = _user_snapshot(update)
    show_all = _can_view_all_cart_log(config, user_id)
    log = get_cart_log(context, config.cart_log_path)
    if show_all:
        entries = log.read_recent(config.cart_log_display_limit)
        title = "Журнал добавлений из бота"
    else:
        entries = log.read_recent(
            config.cart_log_display_limit,
            telegram_user_id=user_id,
        )
        title = "Ваши добавления в корзину"
    text = format_cart_log(entries, title=title, show_user=show_all)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


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
        text = format_flavor_search(result)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await status.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("flavor search failed for %r", query)
        await status.edit_text("Ошибка при поиске. Попробуйте позже.")
