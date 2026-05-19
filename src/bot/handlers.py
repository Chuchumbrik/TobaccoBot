"""Обработчики команд Telegram."""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.config import BotConfig
from bot.formatters import format_check_result, format_flavor_search, format_help
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


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(format_help(), parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(format_help(), parse_mode=ParseMode.HTML)


SEARCH_COMMAND_HINT = (
    "<b>Поиск по вкусу</b>\n\n"
    "Напишите вкус и граммовку:\n"
    "• <code>/search малина 200</code>\n"
    "• <code>/search арбуз дыня</code>\n"
    "• <code>/search кокос | must have</code> — только бренд\n"
    "• <code>/poisk</code> или <code>/поиск</code>\n\n"
    "Или без команды: <code>вкус малина</code>"
)


async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ /search <вкус> — основная команда поиска """
    query = " ".join(context.args).strip() if context.args else ""
    if not query and update.message:
        await update.message.reply_text(SEARCH_COMMAND_HINT, parse_mode=ParseMode.HTML)
        return
    await _run_flavor_search(update, context, query)


async def cmd_flavor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Алиас для /search."""
    await cmd_search(update, context)


async def handle_cyrillic_search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """ /поиск и /vкус — кириллица не проходит CommandHandler API """
    if not update.message or not update.message.text:
        return
    m = re.match(r"(?i)^/(поиск|vкус)(?:@\w+)?(?:\s+(.*))?$", update.message.text.strip())
    if not m:
        return
    query = (m.group(2) or "").strip()
    if not query:
        await update.message.reply_text(SEARCH_COMMAND_HINT, parse_mode=ParseMode.HTML)
        return
    await _run_flavor_search(update, context, query)


async def handle_flavor_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сообщение вида: вкус малина 200"""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    lower = text.lower()
    for prefix in ("вкус ", "вкус:", "flavor ", "flavor:", "поиск ", "поиск:", "search ", "search:"):
        if lower.startswith(prefix):
            query = text[len(prefix) :].strip()
            if query:
                await _run_flavor_search(update, context, query)
            return


async def handle_single_line(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Одна строка — проверка конкретной позиции."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if "\n" in text or text.startswith("/"):
        return
    lower = text.lower()
    if lower.startswith(("вкус", "flavor", "поиск", "search")):
        return

    status = await update.message.reply_text("Проверяю…")
    try:
        results = get_service(context).check_list([text])
        await status.edit_text(
            format_check_result(results[0]),
            parse_mode=ParseMode.HTML,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа: {exc}")
    except Exception:
        logger.exception("single check failed")
        await status.edit_text("Ошибка при проверке.")


async def handle_check_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Многострочный список для проверки наличия."""
    if not update.message or not update.message.text:
        return

    lines = [ln.strip() for ln in update.message.text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return

    config = get_config(context)
    if len(lines) > config.check_list_max_lines:
        await update.message.reply_text(
            f"Слишком много строк (макс. {config.check_list_max_lines})."
        )
        return

    status = await update.message.reply_text(f"Проверяю {len(lines)} позиций…")
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
    status = await update.message.reply_text(f"Ищу «{query}»…")

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
