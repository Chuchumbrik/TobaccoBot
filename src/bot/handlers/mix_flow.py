"""Рецепты миксов (LLM): генерация, подбор из каталога, сборка в корзину.

Ключевые публичные функции:
  _run_advise_mix(update, context, description)        — из текстового сообщения
  _run_advise_mix_from_chat(context, chat_id, description) — из inline-колбэка
  _callback_mix_build(update, context, data)           — кнопка «Собрать микс»

Внутренняя реализация использует _do_advise_mix(), которая содержит
единую логику (ранее дублировалась в _run_advise_mix и _run_advise_mix_from_chat).
"""

from __future__ import annotations

import logging

from telegram import InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.action_context import (
    get_exclusions,
    get_flavor_hits,
    get_mix_recipes,
    save_advise_description,
    save_exclusions,
    save_flavor_search,
    save_mix_recipes,
    save_was_mix,
)
from bot.search_filters import filter_hits, hit_excluded, parse_exclusions
from bot.search_log import log_search
from bot.formatters import format_cart_batch
from bot.inline_keyboards import (
    CB_MIX_BUILD,
    after_cart_keyboard,
    mix_results_keyboard,
)
from bot.progress import StepStatus
from bot.handlers.common import (
    finish_status,
    get_config,
    get_service,
    log_cart_batch,
    send_status,
    user_snapshot,
)
from bot import service_async as osh
from bot.catalog_search import search_terms_as_map, search_terms_parallel
from bot.llm_gate import (
    begin_request_llm_budget,
    check_llm_allowed,
    consume_request_llm_budget,
    end_request_llm_budget,
)
from oshisha.auth import OshishaAuthError
from oshisha.llm import (
    extract_flavor_intent,
    get_llm_trace,
    recommend_mix,
    start_llm_trace,
)
from oshisha.taxonomy import resolve_component

logger = logging.getLogger(__name__)

# Минимальный match_score для компонентов микса.
# Каталог возвращает хиты с min_score=0.42 — это слишком низко для миксов.
# Порог 0.65 отсекает нерелевантные замены типа «молочный шоколад» → Cola,
# сохраняя реальные вкусовые совпадения (карамель, ваниль, ягоды).
MIX_MIN_MATCH_SCORE: float = 0.65


# ── Fallback: поиск через taxonomy без LLM ───────────────────────────────────

async def _taxonomy_search_text(
    context: ContextTypes.DEFAULT_TYPE,
    intent: str,
) -> str:
    """Прямой поиск по каталогу через taxonomy — без LLM.

    Используется как fallback когда recommend_mix не смог сгенерировать рецепты.
    intent: очищенный запрос, напр. «кислый» или «ягодный».
    """
    config = get_config(context)
    service = get_service(context)
    search_terms = resolve_component(intent, max_terms=4)
    logger.info("_taxonomy_search_text: %r → terms=%s", intent, search_terms)

    merged, _ = await search_terms_parallel(
        service,
        search_terms,
        limit_per_term=config.flavor_search_limit,
    )
    merged = merged[: config.flavor_search_limit]

    excluded = get_exclusions(context)
    if excluded:
        before = len(merged)
        merged = filter_hits(merged, excluded)
        logger.info(
            "_taxonomy_search_text exclusion filter: %s  %d→%d hits",
            excluded, before, len(merged),
        )

    if not merged:
        excl_note = f"\n🚫 Исключено: {', '.join(excluded)}" if excluded else ""
        return (
            f"😔 По запросу «{intent}» ничего не нашлось.{excl_note}\n"
            "Попробуй описать иначе или используй /search."
        )

    merged.sort(key=lambda h: (h.status != "есть", -h.match_score))
    merged = merged[: config.flavor_search_limit]
    in_stock = sum(1 for h in merged if h.status == "есть")

    lines = [
        f"🔍 <b>По запросу «{intent}»:</b> {len(merged)} (в наличии: {in_stock})\n"
        f"<i>Миксы сгенерировать не удалось — показываю подходящие вкусы:</i>\n"
    ]
    for i, h in enumerate(merged, 1):
        icon = "✅" if h.status == "есть" else "❌"
        brand = f" [{h.brand_display}]" if h.brand_display else ""
        lines.append(f"{i}. {icon} {h.product.name}{brand}")

    return "\n".join(lines)


# ── Подбор компонентов микса из каталога ─────────────────────────────────────

async def _compute_mix_results(
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
    mixes: list[dict],
    *,
    progress: StepStatus | None = None,
) -> tuple[list, list, str, int]:
    """Подбор позиций по рецептам микса (LLM уже вызван снаружи).

    Возвращает (all_hits, mix_recipes, formatted_text, catalog_calls).
    """
    if not mixes:
        return [], [], "", 0

    service = get_service(context)
    all_hits: list = []
    mix_recipes: list[dict] = []
    result_blocks: list[str] = []

    all_terms: list[str] = []
    component_terms: dict[str, list[str]] = {}
    for mix in mixes:
        for component in mix.get("components", []):
            terms = resolve_component(component, max_terms=3)
            component_terms[component] = terms
            all_terms.extend(terms)

    if progress:
        await progress.begin("Поиск ингредиентов")

    term_hits, catalog_calls = await search_terms_as_map(
        service, all_terms, limit_per_term=5
    )

    excluded = get_exclusions(context)

    if progress:
        await progress.begin("Сборка рецептов", note=f"Сборка рецептов ({len(mixes)})")

    globally_used: set[str] = set()

    for mix in mixes:
        name = mix.get("name", "Микс")
        mood = mix.get("mood", "")
        components = mix.get("components", [])
        mix_hit_indices: list[int] = []
        mood_str = f" <i>· {mood}</i>" if mood else ""
        block_lines = [f"<b>🎨 {name}</b>{mood_str}"]
        used_in_recipe: set[str] = set()

        for component in components:
            try:
                search_terms = component_terms.get(component) or resolve_component(
                    component, max_terms=3
                )
                logger.debug(
                    "mix component %r → search_terms=%s", component, search_terms
                )

                best = None
                used_term = component
                found_oos = False

                for term in search_terms:
                    hits = term_hits.get(term) or []
                    if not hits:
                        continue
                    for h in hits:
                        key = h.product.name.lower().strip()
                        if excluded and hit_excluded(h, excluded):
                            continue
                        if h.status != "есть":
                            found_oos = True
                            continue
                        if key not in used_in_recipe:
                            if best is None or key not in globally_used:
                                best = h
                                used_term = term
                            if key not in globally_used:
                                break
                    if best is not None and best.product.name.lower().strip() not in used_in_recipe:
                        break

                if best:
                    product_key = best.product.name.lower().strip()
                    if product_key in used_in_recipe:
                        logger.debug("Skipping duplicate in recipe: %r", best.product.name)
                        continue

                    # ── Порог релевантности ───────────────────────────────────
                    if best.match_score < MIX_MIN_MATCH_SCORE:
                        logger.info(
                            "mix component %r: weak match score=%.3f → %r — treating as not found",
                            component, best.match_score, best.product.name,
                        )
                        block_lines.append(f"  • ⭕ {component} — не найден в каталоге")
                        continue

                    used_in_recipe.add(product_key)
                    globally_used.add(product_key)

                    brand = f" [{best.brand_display}]" if best.brand_display else ""
                    hint = f" <i>({used_term})</i>" if used_term != component else ""
                    logger.debug(
                        "mix component %r → %r score=%.3f",
                        component, best.product.name, best.match_score,
                    )
                    block_lines.append(f"  • ✅ {best.product.name}{brand}{hint}")
                    mix_hit_indices.append(len(all_hits))
                    all_hits.append(best)
                else:
                    reason = "нет в наличии" if found_oos else "не найден в каталоге"
                    block_lines.append(f"  • ⭕ {component} — {reason}")

            except Exception:
                logger.warning("mix component search failed: %r", component)
                block_lines.append(f"  • ❓ {component}")

        result_blocks.append("\n".join(block_lines))
        mix_recipes.append({"name": name, "indices": mix_hit_indices})

    # Сохраняем в контекст
    save_advise_description(context, description)
    save_mix_recipes(context, mix_recipes)
    if all_hits:
        from oshisha.flavor_search import FlavorSearchResult
        from oshisha.query_parser import ParsedQuery as PQ
        synthetic = FlavorSearchResult(
            query=description,
            parsed=PQ(raw=description, flavor_text=description),
            hits=all_hits,
        )
        save_flavor_search(context, synthetic)

    header = f"🎨 <b>Рецепты миксов для:</b> {description}\n\n"
    text = header + "\n\n".join(result_blocks)
    if len(text) > 4000:
        text = text[:3990] + "\n…"

    logger.info(
        "_compute_mix_results: description=%r  recipes=%d  in_stock_hits=%d",
        description, len(mix_recipes), len(all_hits),
    )
    return all_hits, mix_recipes, text, catalog_calls


# ── Лог микс-поиска ──────────────────────────────────────────────────────────

def _log_mix_search(
    *,
    user_id: int,
    description: str,
    results_count: int,
    in_stock_count: int,
    top_names: list[str],
    catalog_calls: int,
) -> None:
    trace = get_llm_trace()
    log_search(
        user_id=user_id,
        query=description,
        intent=extract_flavor_intent(description),
        search_type="mix",
        results_count=results_count,
        in_stock_count=in_stock_count,
        top_names=top_names,
        llm_backend=trace.backend if trace else None,
        llm_calls=trace.calls if trace else None,
        llm_total_ms=trace.total_ms if trace else None,
        catalog_calls=catalog_calls,
    )


# ── Ядро флоу миксов (единая реализация) ─────────────────────────────────────

async def _do_advise_mix(
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
    *,
    update: Update | None = None,
    chat_id: int | None = None,
) -> None:
    """Ядро флоу подбора миксов.

    Принимает либо update (из текстового сообщения), либо chat_id (из inline-колбэка).
    Ровно один из параметров должен быть передан.
    """
    assert (update is not None) or (chat_id is not None), \
        "_do_advise_mix: нужен update или chat_id"

    # ── Парсим исключения ─────────────────────────────────────────────────────
    cleaned_desc, new_excluded = parse_exclusions(description)
    save_exclusions(context, new_excluded)
    llm_desc = cleaned_desc if cleaned_desc else description

    config = get_config(context)

    # ── Инициализируем статус-сообщение (пути update vs chat_id) ─────────────
    if update is not None:
        user_id, _, _ = user_snapshot(update)
        status = await send_status(update, "🎨 Подбираю рецепты микса…")
    else:
        user_id = chat_id  # type: ignore[assignment]
        status = await context.bot.send_message(chat_id, "🎨 Подбираю рецепты микса…")

    progress = StepStatus(
        status,
        f"🎨 <b>Подбираю рецепты:</b> <i>«{description}»</i>",
        ["Генерация рецептов", "Поиск ингредиентов", "Сборка рецептов"],
    )
    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)

    async def _finish(
        text: str,
        *,
        pm: str | None = ParseMode.HTML,
        markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        """Унифицированная финальная отправка результата."""
        if update is not None:
            await finish_status(status, update, text, parse_mode=pm, inline_markup=markup)
        else:
            try:
                await status.edit_text(text, parse_mode=pm)
                if markup is not None:
                    await status.edit_reply_markup(reply_markup=markup)
            except Exception:
                pass

    try:
        if not check_llm_allowed(context, config, user_id, calls=1):
            await _finish("Слишком много запросов к ИИ за час. Попробуйте позже.", pm=None)
            return

        await progress.begin("Генерация рецептов")
        mixes: list[dict] = []
        if consume_request_llm_budget(context, 1):
            mixes = await recommend_mix(llm_desc)

        if not mixes:
            intent = extract_flavor_intent(llm_desc)
            search_desc = intent if len(intent) >= 2 else llm_desc
            logger.info(
                "_do_advise_mix fallback: %r → taxonomy(%r)",
                description, search_desc,
            )
            fallback_text = await _taxonomy_search_text(context, search_desc)
            save_was_mix(context, False)
            _log_mix_search(
                user_id=user_id,
                description=description,
                results_count=0,
                in_stock_count=0,
                top_names=[],
                catalog_calls=0,
            )
            await _finish(fallback_text)
            return

        save_was_mix(context, True)
        all_hits, mix_recipes, text, catalog_calls = await _compute_mix_results(
            context, description, mixes, progress=progress
        )
        _log_mix_search(
            user_id=user_id,
            description=description,
            results_count=len(all_hits),
            in_stock_count=sum(1 for h in all_hits if h.status == "есть"),
            top_names=[h.product.name for h in all_hits[:3]],
            catalog_calls=catalog_calls,
        )
        await _finish(text, markup=mix_results_keyboard(mix_recipes))

    except Exception:
        logger.exception("_do_advise_mix failed for %r", description)
        await _finish("Ошибка при подборе микса.", pm=None)
    finally:
        end_request_llm_budget()


# ── Публичные точки входа (тонкие обёртки над _do_advise_mix) ────────────────

async def _run_advise_mix(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
) -> None:
    """Флоу подбора микса из текстового сообщения."""
    await _do_advise_mix(context, description, update=update)


async def _run_advise_mix_from_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    description: str,
) -> None:
    """Флоу подбора микса из inline-колбэка (без update.message)."""
    await _do_advise_mix(context, description, chat_id=chat_id)


# ── Callback: сборка конкретного микса в корзину ──────────────────────────────

async def _callback_mix_build(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    data: str,
) -> None:
    """Собрать конкретный микс в корзину (все его компоненты сразу)."""
    query = update.callback_query
    if not query:
        return
    try:
        mix_idx = int(data.removeprefix(CB_MIX_BUILD))
    except ValueError:
        await query.answer("Некорректная кнопка")
        return

    mix_recipes = get_mix_recipes(context)
    if mix_idx < 0 or mix_idx >= len(mix_recipes):
        await query.answer("Рецепты устарели — повторите запрос", show_alert=True)
        return

    mix = mix_recipes[mix_idx]
    hit_indices = mix.get("indices", [])
    mix_name = mix.get("name", f"Микс {mix_idx + 1}")

    if not hit_indices:
        await query.answer("Нет доступных позиций для добавления", show_alert=True)
        return

    hits = get_flavor_hits(context)
    await query.answer(f"🛒 Добавляю «{mix_name}»…")

    chat_id = query.message.chat_id if query.message else None
    if not chat_id:
        return

    status = await context.bot.send_message(chat_id, f"🛒 Добавляю микс «{mix_name}»…")
    try:
        batch = await osh.add_flavor_hits_to_cart(get_service(context), hits, hit_indices)
        log_cart_batch(update, context, batch)
        text = format_cart_batch(batch)
        if len(text) > 4000:
            text = text[:3990] + "\n…"
        await status.edit_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=after_cart_keyboard(),
        )
        logger.info(
            "mix_build: mix=%r  indices=%s  items=%d",
            mix_name, hit_indices, len(batch.items) if batch else 0,
        )
    except OshishaAuthError as exc:
        await status.edit_text(f"Ошибка входа на Oshisha: {exc}")
    except Exception:
        logger.exception("_callback_mix_build failed for mix=%r", mix_name)
