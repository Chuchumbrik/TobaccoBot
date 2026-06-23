"""Поиск по вкусу."""

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
    get_exclusions,
    get_flavor_hits,
    get_flavor_query,
    get_mix_recipes,
    get_pick_message_id,
    save_advise_description,
    save_clarify_state,
    save_checks,
    save_exclusions,
    save_flavor_search,
    save_flavor_suggest_meta,
    get_flavor_suggest_meta,
    save_mix_recipes,
    set_pick_message_id,
)
from bot.search_filters import filter_hits, parse_exclusions
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
    CB_FLAVOR_GROUP,
    CB_FLAVOR_GEN_MIX,
    CB_FLAVOR_SUGGEST,
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
    weight_picker_keyboard,
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
    PROMPT_FLAVOR,
    PROMPT_FOOTER,
    clear_mode,
    get_mode,
    has_mode,
    set_mode,
)
from bot.messages import format_welcome
from bot.handlers.mix_flow import _run_advise_mix_from_chat
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
from bot.llm_gate import (
    begin_request_llm_budget,
    check_llm_allowed,
    consume_request_llm_budget,
    end_request_llm_budget,
)
from oshisha.llm import (
    get_learned_count,
    get_llm_trace,
    normalize_query,
    save_learned_mapping,
    start_llm_trace,
    suggest_alternatives,
)
from oshisha.query_parser import parse_query

logger = logging.getLogger(__name__)

async def cmd_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        await prompt_flavor(update, context)
        return
    clear_mode(context)
    await _run_flavor_search(update, context, query)

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
        await prompt_flavor(update, context)
        return
    clear_mode(context)
    await _run_flavor_search(update, context, query)

async def _run_flavor_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    query: str,
) -> None:
    if not update.message:
        return

    # Новый поиск — сбрасываем advise-контекст, чтобы следующие «без мяты» и т.п.
    # не уточняли старый результат советника через contextual refine.
    save_advise_description(context, "")

    # ── Парсим исключения из запроса ─────────────────────────────────────────
    # Новый независимый поиск — сбрасываем накопленные, берём только из текущего запроса
    cleaned_query, new_excluded = parse_exclusions(query)
    combined_excluded = new_excluded
    save_exclusions(context, combined_excluded)
    # Для поиска и LLM используем очищенный запрос (без "без Адалии" и т.п.)
    search_query = cleaned_query if cleaned_query else query

    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    status = await send_status(update, f"🔍 Ищу «{query}»…")
    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)
    catalog_calls = 0

    try:
        # ── Шаг 1: нормализация через LLM (только если словарь не распознал вкус) ──
        pre = parse_query(search_query)
        normalized = search_query
        if not pre.flavor_keys:
            if not check_llm_allowed(context, config, user_id, calls=1):
                await finish_status(
                    status, update,
                    "Слишком много запросов к ИИ за час. Попробуйте позже или /search без уточнения.",
                    parse_mode=None,
                )
                return
            try:
                if not consume_request_llm_budget(context, 1):
                    normalized = search_query
                else:
                    await status.edit_text(f"🤖 Уточняю запрос: «{search_query}»…")
                    normalized = await normalize_query(search_query)
                if normalized != search_query:
                    logger.info("LLM normalized %r → %r", search_query, normalized)
                    await status.edit_text(
                        f"🔍 Ищу «{normalized}»…\n<i>Запрос уточнён: «{search_query}» → «{normalized}»</i>",
                        parse_mode=ParseMode.HTML,
                    )
                else:
                    await status.edit_text(f"🔍 Ищу «{search_query}»…")
            except Exception:
                logger.warning("LLM normalization skipped due to error")
                normalized = search_query

        # ── Шаг 2: поиск по каталогу ─────────────────────────────────────────────
        result = await osh.search_flavor(
            get_service(context),
            normalized,
            limit=config.flavor_search_limit,
        )
        catalog_calls = 1

        # ── Шаг 2.1: применяем исключения ────────────────────────────────────────
        display_hits = result.hits
        if combined_excluded:
            before = len(display_hits)
            display_hits = filter_hits(display_hits, combined_excluded)
            logger.info(
                "flavor exclusion filter: %s  %d→%d hits",
                combined_excluded, before, len(display_hits),
            )
            # Создаём копию результата с отфильтрованными хитами для сохранения
            from dataclasses import replace as dc_replace
            filtered_result = dc_replace(result, hits=display_hits)
        else:
            filtered_result = result

        save_flavor_search(context, filtered_result)
        save_flavor_suggest_meta(
            context,
            normalized=normalized,
            parsed_summary=result.parsed.summary(),
        )

        in_stock = [h for h in display_hits if h.status == "есть"]
        trace = get_llm_trace()
        log_search(
            user_id=user_id,
            query=query,
            intent=normalized,
            search_type="flavor",
            results_count=len(display_hits),
            in_stock_count=len(in_stock),
            top_names=[h.product.name for h in display_hits[:3]],
            llm_backend=trace.backend if trace else None,
            llm_calls=trace.calls if trace else None,
            llm_total_ms=trace.total_ms if trace else None,
            catalog_calls=catalog_calls,
        )

        # ── Шаг 2.5: сохраняем успешный маппинг в кеш ────────────────────────────
        if normalized != search_query:
            is_new = save_learned_mapping(search_query, normalized)
            if is_new:
                logger.info(
                    "Learned mapping saved. Total: %d", get_learned_count()
                )

        text = format_flavor_search(filtered_result)
        # Добавляем примечание об исключениях
        if combined_excluded:
            excl_note = f"\n🚫 <i>Исключено: {', '.join(combined_excluded)}</i>"
            # Вставляем после первой строки заголовка
            text = text + excl_note if len(text) + len(excl_note) <= 4000 else text

        if len(text) > 4000:
            text = text[:3990] + "\n…"

        # Показываем кнопку «Сгенерировать микс» если есть хиты
        keyboard = (
            flavor_search_keyboard_with_mix(display_hits, excluded=combined_excluded or None)
            if display_hits
            else flavor_search_keyboard(display_hits, excluded=combined_excluded or None)
        )
        await finish_status(
            status,
            update,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            inline_markup=keyboard,
        )
    except OshishaAuthError as exc:
        await finish_status(status, update, f"Ошибка входа на Oshisha: {exc}", parse_mode=None)
    except Exception:
        logger.exception("flavor search failed for %r", query)
        await finish_status(
            status, update, "Ошибка при поиске. Попробуйте позже.", parse_mode=None
        )
    finally:
        end_request_llm_budget()

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

async def _callback_flavor_suggest(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Кнопка «Подобрать альтернативы» после пустого поиска."""
    query = update.callback_query
    if not query or not query.message:
        return
    config = get_config(context)
    user_id = query.from_user.id if query.from_user else 0
    norm, parsed = get_flavor_suggest_meta(context)
    if not norm:
        await query.answer("Нет данных для подбора", show_alert=True)
        return
    if not check_llm_allowed(context, config, user_id, calls=1):
        await query.answer("Лимит ИИ на час исчерпан", show_alert=True)
        return
    if not consume_request_llm_budget(context, 1):
        await query.answer("Слишком много шагов ИИ в одном запросе", show_alert=True)
        return
    await query.answer()
    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)
    try:
        await query.message.edit_text("🤖 Подбираю альтернативы…")
        suggest_result = await suggest_alternatives(norm, parsed)
        trace = get_llm_trace()
        log_search(
            user_id=user_id,
            query=norm,
            intent="suggest",
            search_type="flavor_suggest",
            results_count=0,
            in_stock_count=0,
            top_names=[],
            llm_backend=trace.backend if trace else None,
            llm_calls=trace.calls if trace else None,
            llm_total_ms=trace.total_ms if trace else None,
            catalog_calls=0,
        )
        from oshisha.flavor_search import FlavorSearchResult
        from oshisha.query_parser import ParsedQuery as PQ

        empty_result = FlavorSearchResult(
            query=norm,
            parsed=PQ(raw=norm, flavor_text=norm),
            hits=[],
        )
        base = format_flavor_search(empty_result)

        alts = suggest_result.get("alternatives", [])
        reason = suggest_result.get("reason", "")
        if alts:
            lines = ["\n💡 <b>Попробуй похожие запросы:</b>"]
            if reason:
                lines.append(f"<i>{reason}</i>")
            for a in alts:
                q_text = a.get("query", "")
                hint = a.get("hint", "")
                if q_text:
                    hint_part = f" — <i>{hint}</i>" if hint else ""
                    lines.append(f"  • <b>{q_text}</b>{hint_part}")
            text = base + "\n".join(lines)
        else:
            text = base

        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await query.message.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=flavor_search_keyboard([]),
        )
    except Exception:
        logger.exception("flavor suggest failed")
        await query.message.edit_text("Не удалось подобрать альтернативы. Попробуйте другой запрос.")
    finally:
        end_request_llm_budget()


async def _callback_flavor_gen_mix(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Сгенерировать миксы на основе последнего поиска по вкусу."""
    query = update.callback_query
    if not query:
        return
    chat_id = query.message.chat_id if query.message else None
    flavor_query = get_flavor_query(context)
    if not chat_id or not flavor_query:
        await query.answer("Нет активного поиска", show_alert=True)
        return
    await query.answer()
    await _run_advise_mix_from_chat(context, chat_id, flavor_query)

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

async def _callback_flavor_group(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Обработка нажатия «Выбрать N» когда у группы несколько граммовок.

    Если в наличии только один вариант — сразу передаём в _callback_flavor_pick.
    Если вариантов несколько — показываем weight-picker с кнопками граммовок.
    """
    try:
        group_index = int(data.removeprefix(CB_FLAVOR_GROUP))
    except ValueError:
        if update.callback_query:
            await update.callback_query.answer("Некорректная кнопка")
        return

    hits = get_flavor_hits(context)
    if not hits:
        if update.callback_query:
            await update.callback_query.answer(
                "Результаты устарели — повторите поиск", show_alert=True
            )
        return

    from bot.weight_groups import group_hits as _group_hits
    groups = _group_hits(hits)
    if group_index < 0 or group_index >= len(groups):
        if update.callback_query:
            await update.callback_query.answer(
                "Результаты устарели — повторите поиск", show_alert=True
            )
        return

    group = groups[group_index]
    in_stock = group.in_stock_variants
    if not in_stock:
        if update.callback_query:
            await update.callback_query.answer("Нет в наличии", show_alert=True)
        return

    if len(in_stock) == 1:
        # Один вариант в наличии — обычный флоу подтверждения
        await _callback_flavor_pick(update, context, f"{CB_FLAVOR_PICK}{in_stock[0].hit_index}")
        return

    # Несколько граммовок — показываем weight-picker
    query = update.callback_query
    if not query:
        return

    from bot.formatters import format_flavor_weight_group
    text = format_flavor_weight_group(group)
    await _show_pick_message(
        update,
        context,
        text=text,
        reply_markup=weight_picker_keyboard(group),
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
        batch = await osh.add_flavor_hits_to_cart(get_service(context), hits, [index])
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
        logger.exception("callback flavor cart failed")
        await query.answer("Ошибка добавления", show_alert=True)
