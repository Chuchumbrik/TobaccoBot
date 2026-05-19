"""Обработчики команд Telegram."""

from __future__ import annotations

import logging
import re

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.action_context import (
    clear_action_context,
    clear_pick_message_id,
    get_checks,
    get_flavor_hits,
    get_pick_message_id,
    save_checks,
    save_flavor_search,
    set_pick_message_id,
)
from bot.cart_log import entries_from_batch, format_session_started, get_cart_log
from bot.config import BotConfig
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
    CB_BACK_CHECK,
    CB_BACK_FLAVOR,
    CB_CANCEL,
    CB_CHECK_CONFIRM,
    CB_CHECK_PICK,
    CB_DISMISS,
    CB_FLAVOR_CONFIRM,
    CB_FLAVOR_PICK,
    CB_SEARCH_AGAIN,
    _in_stock_check_indices,
    _in_stock_flavor_indices,
    check_confirm_keyboard,
    check_results_keyboard,
    flavor_confirm_keyboard,
    flavor_search_keyboard,
    inline_cancel_keyboard,
)
from bot.keyboards import (
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


async def _send_status(update: Update, text: str) -> Message | None:
    """Промежуточное сообщение без клавиатуры — его можно редактировать."""
    if not update.message:
        return None
    return await update.message.reply_text(text)


async def _send_result_message(
    status: Message | None,
    update: Update,
    text: str,
    *,
    text_kwargs: dict,
    inline_markup: InlineKeyboardMarkup | None,
) -> None:
    """Отправить результат новым сообщением, если редактирование недоступно."""
    send_kwargs = dict(text_kwargs)
    if inline_markup is not None:
        send_kwargs["reply_markup"] = inline_markup
    if status is not None:
        await status.chat.send_message(text, **send_kwargs)
    elif update.message:
        await update.message.reply_text(text, **send_kwargs)


async def _finish_status(
    status: Message | None,
    update: Update,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
    disable_web_page_preview: bool = False,
    inline_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Заменить «Проверяю…» на результат; при ошибке edit — новое сообщение."""
    text_kwargs: dict = {}
    if parse_mode is not None:
        text_kwargs["parse_mode"] = parse_mode
    if disable_web_page_preview:
        text_kwargs["disable_web_page_preview"] = True

    if status is None:
        await _send_result_message(
            status, update, text, text_kwargs=text_kwargs, inline_markup=inline_markup
        )
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
            return
        if "can't be edited" in err or "message to edit not found" in err:
            await _send_result_message(
                status, update, text, text_kwargs=text_kwargs, inline_markup=inline_markup
            )
            return
        raise


async def _reply(
    update: Update,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
    with_menu: bool = True,
) -> None:
    if not update.message:
        return
    kwargs: dict = {}
    if with_menu:
        kwargs["reply_markup"] = main_menu_keyboard()
    await update.message.reply_text(
        text,
        parse_mode=parse_mode,
        **kwargs,
    )


async def _reply_step(
    update: Update,
    text: str,
    *,
    parse_mode: str | None = ParseMode.HTML,
) -> None:
    """Подсказка шага сценария: inline «Отмена» под сообщением."""
    if not update.message:
        return
    await update.message.reply_text(
        text,
        parse_mode=parse_mode,
        reply_markup=inline_cancel_keyboard(),
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
    await _reply_step(update, PROMPT_FLAVOR)


async def _prompt_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_SINGLE)
    await _reply_step(update, PROMPT_SINGLE)


async def _prompt_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_LIST)
    config = get_config(context)
    extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк за раз.</i>"
    await _reply_step(update, PROMPT_LIST + extra)


async def _prompt_cart_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_CART_SINGLE)
    await _reply_step(update, PROMPT_CART_SINGLE)


async def _prompt_cart_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    set_mode(context, MODE_CART_LIST)
    config = get_config(context)
    extra = f"\n\n<i>Максимум {config.check_list_max_lines} строк.</i>"
    await _reply_step(update, PROMPT_CART_LIST + extra)


def _log_cart_batch(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    batch,
) -> None:
    user_id, username, full_name = _user_snapshot(update)
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


async def cmd_logreset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    clear_mode(context)
    await _run_log_reset(update, context)


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
        clear_action_context(context)
        await _reply(update, "Шаг отменён.\n\n" + format_welcome())
        return
    if text == BTN_MENU:
        clear_mode(context)
        clear_action_context(context)
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
                "Нужна <b>одна</b> строка. Несколько — команда /cartlist.",
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
    status = await _send_status(update, "Проверяю…")
    try:
        results = get_service(context).check_list([line])
        save_checks(context, results)
        inline = check_results_keyboard(results)
        await _finish_status(
            status,
            update,
            format_check_results(results),
            parse_mode=ParseMode.HTML,
            inline_markup=inline,
        )
    except OshishaAuthError as exc:
        await _finish_status(status, update, f"Ошибка входа: {exc}", parse_mode=None)
    except Exception:
        logger.exception("single check failed")
        await _finish_status(status, update, "Ошибка при проверке.", parse_mode=None)


async def _run_list_check(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    lines: list[str],
) -> None:
    if not update.message:
        return
    status = await _send_status(update, f"Проверяю {len(lines)} позиций…")
    try:
        results = get_service(context).check_list(lines)
        save_checks(context, results)
        text = format_check_results(results)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await _finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            inline_markup=check_results_keyboard(results),
        )
    except OshishaAuthError as exc:
        await _finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("check_list failed")
        await _finish_status(status, update, "Ошибка при проверке списка.", parse_mode=None)


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
    status = await _send_status(update, label)
    try:
        batch = get_service(context).add_to_cart(lines)
        _log_cart_batch(update, context, batch)
        text = format_cart_batch(batch)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await _finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await _finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("add_to_cart failed")
        await _finish_status(
            status, update, "Ошибка при добавлении в корзину.", parse_mode=None
        )


async def _run_cart_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    status = await _send_status(update, "Загружаю корзину с сайта…")
    try:
        cart = get_service(context).view_cart()
        await _finish_status(
            status,
            update,
            format_site_cart(cart),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except OshishaAuthError as exc:
        await _finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("view_cart failed")
        await _finish_status(status, update, "Не удалось загрузить корзину.", parse_mode=None)


async def _run_cart_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = _user_snapshot(update)
    show_all = _can_view_all_cart_log(config, user_id)
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
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True,
    )


async def _run_log_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    config = get_config(context)
    user_id, username, full_name = _user_snapshot(update)
    if not _can_view_all_cart_log(config, user_id):
        await _reply(
            update,
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
    await _reply(
        update,
        f"🔄 <b>Новый заказ №{state.session_id}</b>\n\n"
        f"Предыдущий заказ №{prev.session_id} закрыт "
        f"({prev_count} записей в журнале).\n"
        f"Сейчас: <b>{format_session_started(state)}</b> (МСК)\n"
        f"Инициатор: {who}\n\n"
        "Новые добавления в корзину попадут в этот заказ. "
        "Смотреть — «📜 Журнал».",
    )


async def _run_flavor_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    if not update.message:
        return

    config = get_config(context)
    status = await _send_status(update, f"Ищу «{query}»…")

    try:
        result = get_service(context).search_flavor(
            query,
            limit=config.flavor_search_limit,
        )
        save_flavor_search(context, result)
        text = format_flavor_search(result)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await _finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            inline_markup=flavor_search_keyboard(result.hits),
        )
    except OshishaAuthError as exc:
        await _finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("flavor search failed for %r", query)
        await _finish_status(
            status, update, "Ошибка при поиске. Попробуйте позже.", parse_mode=None
        )


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
                "Шаг отменён.\n\n" + format_welcome(),
                parse_mode=ParseMode.HTML,
                reply_markup=main_menu_keyboard(),
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

    if data.startswith(CB_FLAVOR_PICK):
        await _callback_flavor_pick(update, context, data)
        return

    if data.startswith(CB_FLAVOR_CONFIRM):
        await _callback_flavor_confirm(update, context, data)
        return

    if data == CB_BACK_FLAVOR:
        await _callback_back_to_list(update, context)
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

    await query.answer()


async def _prompt_flavor_from_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    set_mode(context, MODE_FLAVOR)
    await context.bot.send_message(
        chat_id,
        PROMPT_FLAVOR,
        parse_mode=ParseMode.HTML,
        reply_markup=inline_cancel_keyboard(),
    )


async def _remove_pick_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    pick_id = get_pick_message_id(context)
    if pick_id is None:
        return
    try:
        await context.bot.delete_message(chat_id, pick_id)
    except BadRequest:
        pass
    clear_pick_message_id(context)


async def _show_pick_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    text: str,
    reply_markup,
) -> None:
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat_id if query.message else None
    if not chat_id:
        return
    await query.answer()
    await _remove_pick_message(context, chat_id)
    msg = await context.bot.send_message(
        chat_id,
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )
    set_pick_message_id(context, msg.message_id)


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


async def _callback_flavor_pick(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    try:
        index = int(data.removeprefix(CB_FLAVOR_PICK))
    except ValueError:
        if update.callback_query:
            await update.callback_query.answer("Некорректная кнопка")
        return

    hits = get_flavor_hits(context)
    if index < 0 or index >= len(hits):
        if update.callback_query:
            await update.callback_query.answer(
                "Результаты устарели — повторите поиск", show_alert=True
            )
        return

    hit = hits[index]
    if hit.status != "есть":
        if update.callback_query:
            await update.callback_query.answer("Нет в наличии", show_alert=True)
        return

    in_stock = _in_stock_flavor_indices(hits)
    text = format_flavor_pick_confirm(
        hit,
        list_number=index + 1,
        variants_in_stock=len(in_stock),
    )
    await _show_pick_message(
        update,
        context,
        text=text,
        reply_markup=flavor_confirm_keyboard(index),
    )


async def _callback_flavor_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        index = int(data.removeprefix(CB_FLAVOR_CONFIRM))
    except ValueError:
        await query.answer("Некорректная кнопка")
        return

    hits = get_flavor_hits(context)
    if index < 0 or index >= len(hits):
        await query.answer("Результаты устарели — повторите поиск", show_alert=True)
        return

    hit = hits[index]
    if hit.status != "есть":
        await query.answer("Нет в наличии", show_alert=True)
        return

    try:
        batch = get_service(context).add_flavor_hits_to_cart(hits, [index])
        _log_cart_batch(update, context, batch)
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
                )
        else:
            msg = item.message if item else "ошибка"
            await query.answer(f"❌ {msg}", show_alert=True)
    except OshishaAuthError as exc:
        await query.answer(f"Ошибка входа: {exc}", show_alert=True)
    except Exception:
        logger.exception("callback flavor cart failed")
        await query.answer("Ошибка добавления", show_alert=True)


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
        batch = get_service(context).add_checks_to_cart(checks, indices=[index])
        _log_cart_batch(update, context, batch)
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
                )
        else:
            msg = item.message if item else "ошибка"
            await query.answer(f"❌ {msg}", show_alert=True)
    except OshishaAuthError as exc:
        await query.answer(f"Ошибка входа: {exc}", show_alert=True)
    except Exception:
        logger.exception("callback check cart failed")
        await query.answer("Ошибка добавления", show_alert=True)
