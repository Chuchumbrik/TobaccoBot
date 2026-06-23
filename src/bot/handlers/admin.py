"""Административные команды."""

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


async def cmd_apply_vocab_patch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/apply_vocab_patch [YYYY-MM-DD] — применить vocab-патч к таксономии.

    Без аргумента применяет самый свежий патч.
    """
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    if not can_view_all_cart_log(config, user_id):
        await reply(update, context, "⛔ Команда доступна только администраторам.")
        return

    from bot.vocab_patch import (  # ленивый импорт — разрываем цикл
        apply_patch, load_latest_patch, load_patch_by_date, format_patch_preview,
    )
    from oshisha.llm import invalidate_vocab_cache
    from oshisha import catalog_cache

    # Определяем какой патч применять
    target_date: str | None = None
    if context.args:
        raw = context.args[0].strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            target_date = raw
        else:
            await reply(update, context, "❌ Неверный формат. Используй: <code>YYYY-MM-DD</code>")
            return

    if target_date:
        patch_path, patch = load_patch_by_date(target_date)
    else:
        patch_path, patch = load_latest_patch()

    if patch is None:
        await reply(update, context, "ℹ️ Патч не найден. Запусти /digest чтобы сгенерировать.")
        return

    # Показываем превью и применяем
    preview = format_patch_preview(patch)
    status = await update.message.reply_text(
        f"{preview}\n\n⏳ Применяю…",
        parse_mode=ParseMode.HTML,
    )

    result = apply_patch(patch)
    added   = result.get("added", [])
    skipped = result.get("skipped", [])
    errors  = result.get("errors", [])

    if added:
        # Сбрасываем кэши чтобы новые ключи сразу заработали
        invalidate_vocab_cache()
        catalog_cache.invalidate()

    parts: list[str] = []
    if added:
        parts.append(f"✅ <b>Добавлено {len(added)} ключей:</b> {', '.join(f'<code>{k}</code>' for k in added)}")
    if skipped:
        parts.append(f"⏭ Пропущено (уже есть): {', '.join(f'<code>{k}</code>' for k in skipped)}")
    if errors:
        parts.append(f"⚠️ Ошибки:\n" + "\n".join(f"  • {e}" for e in errors))
    if not added and not errors:
        parts.append("ℹ️ Нечего применять — все ключи уже в таксономии.")

    result_text = "\n".join(parts)
    if patch_path:
        result_text += f"\n\n<i>Файл: {patch_path.name}</i>"

    await status.edit_text(result_text, parse_mode=ParseMode.HTML)


async def cmd_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/digest [YYYY-MM-DD] — запустить ночной дайджест вручную (только администраторы).

    Без аргумента анализирует вчерашний день.
    С аргументом — указанную дату.
    """
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    if not can_view_all_cart_log(config, user_id):
        await reply(update, context, "⛔ Команда доступна только администраторам.")
        return

    # Парсим опциональный аргумент даты
    target_date: str | None = None
    if context.args:
        raw = context.args[0].strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            target_date = raw
        else:
            await reply(update, context, "❌ Неверный формат даты. Используй: <code>YYYY-MM-DD</code>")
            return

    status = await update.message.reply_text("⏳ Запускаю анализ логов…")
    try:
        from bot.night_digest import run_night_digest  # ленивый импорт — разрываем циклическую зависимость
        result = await run_night_digest(context.application, target_date=target_date)
        if result is None:
            await status.edit_text(
                "ℹ️ Нет данных для анализа (слишком мало запросов или логи отсутствуют).",
                parse_mode=ParseMode.HTML,
            )
        else:
            await status.delete()   # дайджест уже отправлен через run_night_digest
    except Exception:
        logger.exception("cmd_digest failed")
        await status.edit_text("❌ Ошибка при анализе. Проверьте логи сервера.")


async def cmd_update_taxonomy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ручное обновление таксономии вкусов (только для администраторов).

    Запускает scripts/update_taxonomy.py и показывает итог.
    """
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    if not can_view_all_cart_log(config, user_id):
        await reply(update, context, "⛔ Команда доступна только администраторам бота.")
        return

    status = await update.message.reply_text("⏳ Запускаю обновление таксономии вкусов…")
    try:
        _ROOT = Path(__file__).resolve().parents[2]
        script = _ROOT / "scripts" / "update_taxonomy.py"
        proc = await asyncio.create_subprocess_exec(
            sys.executable, str(script),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(_ROOT),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""

        # Извлекаем ключевые строки для краткого отчёта
        lines = output.strip().splitlines()
        summary_lines = [
            ln.split(" INFO ")[-1].split(" WARNING ")[-1]  # убираем timestamp/level
            for ln in lines
            if any(kw in ln for kw in [
                "Итог:", "охвачено", "не охвачено", "Добавлено",
                "Всё охвачено", "Профилей в таксономии", "Вкусов без категории",
            ])
        ]
        summary = "\n".join(summary_lines[-8:]) if summary_lines else output[-800:]

        if proc.returncode == 0:
            from oshisha import catalog_cache
            from oshisha.llm import invalidate_vocab_cache, reload_prompts

            invalidate_vocab_cache()
            catalog_cache.invalidate()
            n_prompts = reload_prompts()
            await status.edit_text(
                f"✅ <b>Таксономия обновлена</b>\n\n<pre>{summary}</pre>"
                f"\n<i>Промпты перезагружены ({n_prompts} файлов)</i>",
                parse_mode=ParseMode.HTML,
            )
        else:
            error_ctx = "\n".join(lines[-5:])
            await status.edit_text(
                f"❌ Ошибка обновления (код {proc.returncode})\n\n<pre>{error_ctx}</pre>",
                parse_mode=ParseMode.HTML,
            )
        logger.info("cmd_update_taxonomy: exit=%d lines=%d", proc.returncode, len(lines))

    except asyncio.TimeoutError:
        await status.edit_text("⏰ Таймаут (600 с). Проверьте логи сервера.")
    except Exception:
        logger.exception("cmd_update_taxonomy failed")
        await status.edit_text("❌ Ошибка запуска скрипта. Проверьте логи.")


async def cmd_reload_prompts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Горячая перезагрузка LLM-промптов из data/prompts/*.txt (только для администраторов).

    Позволяет обновить текст промптов без перезапуска бота.
    """
    if not update.message:
        return
    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    if not can_view_all_cart_log(config, user_id):
        await reply(update, context, "⛔ Команда доступна только администраторам бота.")
        return

    from oshisha.llm import reload_prompts

    n = reload_prompts()
    await reply(
        update,
        context,
        f"✅ <b>Промпты перезагружены</b>: {n} файлов из <code>data/prompts/</code>.\n\n"
        "<i>Следующие LLM-запросы будут использовать новые версии.</i>",
    )
