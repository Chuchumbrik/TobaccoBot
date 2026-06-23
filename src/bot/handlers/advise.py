"""Советник (LLM): подбор вкусов, уточнения, режим clarify."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.action_context import (
    get_exclusions,
    get_was_mix,
    save_advise_description,
    save_clarify_state,
    save_exclusions,
    save_flavor_search,
    save_was_mix,
)
from bot.search_filters import filter_hits, parse_exclusions
from bot.search_log import log_search
from bot.inline_keyboards import (
    ADVISE_PAGE_SIZE,
    advise_keyboard,
    clarify_question_keyboard,
    mix_results_keyboard,
)
from bot.menu_state import (
    MODE_ADVISE,
    MODE_ADVISE_CLARIFY,
    PROMPT_FOOTER,
    clear_mode,
    set_mode,
)
from bot.progress import StepStatus
from bot.handlers.common import (
    finish_status,
    get_config,
    get_service,
    reply,
    send_status,
    user_snapshot,
)
from bot.handlers.mix_flow import (
    _compute_mix_results,
    _log_mix_search,
    _taxonomy_search_text,
)
from bot.catalog_search import merge_flavor_searches
from bot.formatters import format_hit_groups_lines
from bot.weight_groups import group_hits as _group_hits
from bot.llm_gate import (
    begin_request_llm_budget,
    check_llm_allowed,
    consume_request_llm_budget,
    end_request_llm_budget,
    remaining_request_llm_budget,
)
from oshisha.llm import (
    count_catalog_llm_slots,
    extract_flavor_intent,
    get_llm_trace,
    queries_for_catalog,
    recommend_mix,
    recommend_or_clarify,
    refine_queries,
    start_llm_trace,
)
from oshisha.query_parser import parse_query as _parse_query_direct

logger = logging.getLogger(__name__)


def _try_llm_slot(context: ContextTypes.DEFAULT_TYPE):
    def _slot() -> bool:
        if remaining_request_llm_budget(context) <= 0:
            return False
        return consume_request_llm_budget(context, 1)

    return _slot


async def _catalog_queries(
    context: ContextTypes.DEFAULT_TYPE,
    raw_queries: list[str],
) -> list[str]:
    need = count_catalog_llm_slots(raw_queries)
    if need > remaining_request_llm_budget(context):
        logger.info(
            "catalog: LLM normalize need=%d, budget left=%d — лишние без normalize",
            need,
            remaining_request_llm_budget(context),
        )
    queries = await queries_for_catalog(raw_queries, try_llm_slot=_try_llm_slot(context))
    return queries if queries else list(raw_queries)


async def _present_advise_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    status,
    *,
    description: str,
    queries: list[str],
    user_id: int,
    title: str,
    show_llm_queries: bool = True,
    progress: StepStatus | None = None,
) -> None:
    config = get_config(context)
    if progress:
        q_hint = ", ".join(queries[:2])
        await progress.begin("Поиск в каталоге", note=f"Поиск: {q_hint}")
    else:
        await status.edit_text(
            f"🔍 Ищу по каталогу…\n<i>Запросы: {', '.join(queries)}</i>",
            parse_mode=ParseMode.HTML,
        )
    service = get_service(context)
    merged_hits, catalog_calls = await merge_flavor_searches(
        service,
        queries,
        limit=config.flavor_search_limit,
        max_total_hits=config.flavor_search_limit,
    )

    # ── Применяем исключения ──────────────────────────────────────────────────
    excluded = get_exclusions(context)
    if excluded:
        before = len(merged_hits)
        merged_hits = filter_hits(merged_hits, excluded)
        logger.info(
            "advise exclusion filter: %s  %d→%d hits",
            excluded, before, len(merged_hits),
        )

    if not merged_hits:
        excl_note = f"\n🚫 Исключено: {', '.join(excluded)}" if excluded else ""
        await status.edit_text(
            f"😔 По запросу «{description}» ничего не нашлось.{excl_note}\n"
            "Попробуй описать иначе или используй /search."
        )
        return

    save_advise_description(context, description)
    trace = get_llm_trace()
    in_stock_log = sum(1 for h in merged_hits if h.status == "есть")
    log_search(
        user_id=user_id,
        query=description,
        intent=", ".join(queries),
        search_type="advise",
        results_count=len(merged_hits),
        in_stock_count=in_stock_log,
        top_names=[h.product.name for h in merged_hits[:3]],
        llm_backend=trace.backend if trace else None,
        llm_calls=trace.calls if trace else None,
        llm_total_ms=trace.total_ms if trace else None,
        catalog_calls=catalog_calls,
    )

    from oshisha.flavor_search import FlavorSearchResult
    from oshisha.query_parser import ParsedQuery as PQ

    save_flavor_search(
        context,
        FlavorSearchResult(
            query=description,
            parsed=PQ(raw=description, flavor_text=description),
            hits=merged_hits,
        ),
    )

    from oshisha import catalog_cache

    groups = _group_hits(merged_hits)
    total_groups = len(groups)
    in_stock_groups = sum(1 for g in groups if g.status == "есть")
    total_pages = max(1, (total_groups + ADVISE_PAGE_SIZE - 1) // ADVISE_PAGE_SIZE)

    header = f"🎯 <b>{title}:</b> {description}\n"
    header += f"Найдено: {total_groups} (в наличии: {in_stock_groups})"
    if total_pages > 1:
        header += f"  ·  стр. 1/{total_pages}"
    header += "\n"
    if excluded:
        header += f"🚫 <i>Исключено: {', '.join(excluded)}</i>\n"
    if show_llm_queries:
        header += f"<i>Запросы: {', '.join(queries)}</i>\n"
    header += catalog_cache.stock_disclaimer_html() + "\n"

    body_lines = format_hit_groups_lines(merged_hits, page=0, page_size=ADVISE_PAGE_SIZE)
    hint = "\n\n<i>💬 Напиши уточнение прямо в чат — например «без мяты», «только BlackBurn», «покислее»</i>"
    text = header + "\n".join(body_lines) + hint
    if len(text) > 4000:
        text = text[:3990] + "\n…"

    await finish_status(
        status,
        update,
        text,
        parse_mode=ParseMode.HTML,
        inline_markup=advise_keyboard(merged_hits, excluded=excluded or None, page=0),
    )

async def cmd_advise(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Советник: «хочу сладкое и ягодное» → подбор вкусов из каталога."""
    description = " ".join(context.args).strip() if context.args else ""
    if not description:
        set_mode(context, MODE_ADVISE)
        await reply(
            update,
            context,
            "🎯 <b>Советник по вкусам</b>\n\n"
            "Опиши что хочешь — я подберу варианты из каталога.\n\n"
            "<i>Примеры:\n"
            "• хочу сладенькое и ягодное\n"
            "• что-то свежее без мяты\n"
            "• кисло-сладкое, лёгкое</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    clear_mode(context)
    await _run_advise(update, context, description)

async def _run_advise(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    description: str,
) -> None:
    """Основной флоу советника."""
    if not update.message:
        return

    # ── Парсим исключения ─────────────────────────────────────────────────────
    # Новый независимый запрос — сбрасываем накопленные, берём только из текущего
    cleaned_desc, new_excluded = parse_exclusions(description)
    combined_excluded = new_excluded
    save_exclusions(context, combined_excluded)
    # Для LLM используем очищенный запрос, для отображения — оригинал
    llm_desc = cleaned_desc if cleaned_desc else description

    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    status = await send_status(update, f"🎯 Подбираю вкусы для «{description}»…")
    progress = StepStatus(
        status,
        f"🎯 <b>Подбираю вкусы:</b> <i>«{description}»</i>",
        ["Анализ запроса", "Поиск в каталоге"],
    )
    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)
    catalog_calls = 0

    try:
        # ── Шаг 1: определяем стратегию поиска ──────────────────────────────
        await progress.begin("Анализ запроса")

        # Если словарь уже опознал конкретный вкус — LLM только расширит до
        # "клубника и малина и грейпфрут…"; доверяем словарю, пропускаем LLM.
        _pre = _parse_query_direct(llm_desc)
        if _pre.flavor_keys:
            decision = {"type": "search", "queries": [llm_desc]}
        elif not check_llm_allowed(context, config, user_id, calls=1):
            await finish_status(
                status, update,
                "Слишком много запросов к ИИ за час. Попробуйте позже.",
                parse_mode=None,
            )
            return
        elif not consume_request_llm_budget(context, 1):
            decision = {"type": "search", "queries": [llm_desc]}
        else:
            decision = await recommend_or_clarify(llm_desc)

        if decision["type"] == "question":
            # LLM не понял — задаём уточняющий вопрос
            question = decision["question"]
            options = decision.get("options") or []
            save_clarify_state(context, description, is_mix=False)
            set_mode(context, MODE_ADVISE_CLARIFY)
            await status.delete()
            if update.message:
                # Форматируем варианты ответа как подсказку
                opts_text = ""
                if options:
                    opts_text = "\n" + " / ".join(f"<i>{o}</i>" for o in options[:4])
                await update.message.reply_text(
                    f"🤔 <b>Уточни, пожалуйста:</b>\n\n{question}{opts_text}"
                    f"\n\n<i>Исходный запрос: «{description}»</i>"
                    + PROMPT_FOOTER,
                    parse_mode=ParseMode.HTML,
                    reply_markup=clarify_question_keyboard(),
                )
            return

        raw_queries = decision.get("queries") or [llm_desc]
        queries = await _catalog_queries(context, raw_queries)
        save_was_mix(context, False)
        logger.info("Advise %r → catalog queries: %s (excluded=%s)", description, queries, combined_excluded)
        await _present_advise_results(
            update,
            context,
            status,
            description=description,
            queries=queries,
            user_id=user_id,
            title="Подборка по запросу",
            progress=progress,
        )

    except Exception:
        logger.exception("_run_advise failed for %r", description)
        await finish_status(status, update, "Ошибка при подборе. Попробуйте позже.", parse_mode=None)
    finally:
        end_request_llm_budget()

async def _run_advise_refine(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    original: str,
    refinement: str,
) -> None:
    """Уточняет рекомендацию с учётом правки пользователя.

    Если предыдущий результат был миксом (was_mix=True) — генерирует новые
    рецепты через recommend_mix. Иначе — уточняет обычную подборку через
    refine_queries + _present_advise_results.
    """
    if not update.message:
        return

    # ── Парсим исключения из уточнения (мерж с накопленными) ─────────────────
    cleaned_refinement, new_excluded = parse_exclusions(refinement)
    prev_excluded = get_exclusions(context)
    combined_excluded = list(dict.fromkeys(prev_excluded + new_excluded))
    save_exclusions(context, combined_excluded)
    llm_refinement = cleaned_refinement if cleaned_refinement else refinement

    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    was_mix = get_was_mix(context)

    status = await send_status(
        update,
        f"✏️ Уточняю: «{original}» + «{refinement}»…",
    )

    if was_mix:
        progress = StepStatus(
            status,
            f"✏️ <b>Уточняю рецепты миксов</b>\n<i>«{original}» → «{refinement}»</i>",
            ["Анализ уточнения", "Поиск ингредиентов", "Сборка рецептов"],
        )
    else:
        progress = StepStatus(
            status,
            f"✏️ <b>Уточняю подборку</b>\n<i>«{original}» → «{refinement}»</i>",
            ["Анализ уточнения", "Поиск в каталоге"],
        )

    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)

    try:
        if not check_llm_allowed(context, config, user_id, calls=1):
            await finish_status(
                status, update,
                "Слишком много запросов к ИИ за час. Попробуйте позже.",
                parse_mode=None,
            )
            return

        await progress.begin("Анализ уточнения")

        if was_mix:
            # ── Путь: перегенерируем миксы с уточнённым описанием ────────────
            combined_desc = f"{original} ({llm_refinement})"
            mixes: list[dict] = []
            if consume_request_llm_budget(context, 1):
                mixes = await recommend_mix(combined_desc)

            if not mixes:
                intent = extract_flavor_intent(combined_desc)
                search_desc = intent if len(intent) >= 2 else combined_desc
                logger.info(
                    "refine(mix) fallback: %r → taxonomy(%r)", combined_desc, search_desc
                )
                fallback_text = await _taxonomy_search_text(context, search_desc)
                save_was_mix(context, False)
                await finish_status(status, update, fallback_text, parse_mode=ParseMode.HTML)
                return

            all_hits, mix_recipes, text, catalog_calls = await _compute_mix_results(
                context, combined_desc, mixes, progress=progress
            )
            save_was_mix(context, True)
            _log_mix_search(
                user_id=user_id,
                description=combined_desc,
                results_count=len(all_hits),
                in_stock_count=sum(1 for h in all_hits if h.status == "есть"),
                top_names=[h.product.name for h in all_hits[:3]],
                catalog_calls=catalog_calls,
            )
            logger.info(
                "Refine(mix) %r + %r → recipes=%d excluded=%s",
                original, refinement, len(mix_recipes), combined_excluded,
            )
            await finish_status(
                status, update, text,
                parse_mode=ParseMode.HTML,
                inline_markup=mix_results_keyboard(mix_recipes),
            )

        else:
            # ── Путь: уточняем обычную подборку советника ────────────────────
            if consume_request_llm_budget(context, 1):
                refine_result = await refine_queries(original, llm_refinement)
            else:
                refine_result = {"type": "ADD", "queries": [llm_refinement], "excluded_terms": []}
            raw_queries = refine_result.get("queries") or [llm_refinement]
            # LLM мог обнаружить новые исключения в уточнении
            llm_excluded = refine_result.get("excluded_terms") or []
            if llm_excluded:
                merged_excl = list(dict.fromkeys(combined_excluded + llm_excluded))
                save_exclusions(context, merged_excl)
                logger.info("refine: LLM detected exclusions: %s", llm_excluded)
            queries = await _catalog_queries(context, raw_queries)
            logger.info(
                "Refine %r + %r → type=%s queries=%s excluded=%s",
                original, refinement, refine_result.get("type"), queries, combined_excluded,
            )
            new_description = f"{original} ({refinement})"
            await _present_advise_results(
                update,
                context,
                status,
                description=new_description,
                queries=queries,
                user_id=user_id,
                title="Уточнённая подборка",
                show_llm_queries=False,
                progress=progress,
            )

    except Exception:
        logger.exception("_run_advise_refine failed")
        await finish_status(status, update, "Ошибка при уточнении.", parse_mode=None)
    finally:
        end_request_llm_budget()


async def _run_advise_after_clarify(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    original: str,
    answer: str,
) -> None:
    """Ответ на уточняющий вопрос: refine + каталог, без повторного recommend_or_clarify."""
    if not update.message:
        return

    # ── Парсим исключения из ответа на уточнение ─────────────────────────────
    cleaned_answer, new_excluded = parse_exclusions(answer)
    prev_excluded = get_exclusions(context)
    combined_excluded = list(dict.fromkeys(prev_excluded + new_excluded))
    save_exclusions(context, combined_excluded)
    llm_answer = cleaned_answer if cleaned_answer else answer

    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    status = await send_status(
        update,
        f"🎯 Учитываю ответ: «{answer}»…",
    )
    progress = StepStatus(
        status,
        f"🎯 <b>Уточняю по ответу:</b> <i>«{answer}»</i>",
        ["Анализ ответа", "Поиск в каталоге"],
    )
    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)

    try:
        if not check_llm_allowed(context, config, user_id, calls=1):
            await finish_status(
                status, update,
                "Слишком много запросов к ИИ за час. Попробуйте позже.",
                parse_mode=None,
            )
            return
        await progress.begin("Анализ ответа")
        if consume_request_llm_budget(context, 1):
            refine_result = await refine_queries(original, llm_answer)
        else:
            refine_result = {"type": "ADD", "queries": [llm_answer], "excluded_terms": []}
        raw_queries = refine_result.get("queries") or [llm_answer]
        # LLM мог обнаружить исключения в ответе на уточнение
        llm_excluded = refine_result.get("excluded_terms") or []
        if llm_excluded:
            merged_excl = list(dict.fromkeys(combined_excluded + llm_excluded))
            save_exclusions(context, merged_excl)
            logger.info("clarify: LLM detected exclusions: %s", llm_excluded)
        queries = await _catalog_queries(context, raw_queries)
        save_was_mix(context, False)
        description = f"{original} ({answer})" if original else answer
        logger.info(
            "Clarify %r + %r → type=%s queries=%s excluded=%s",
            original, answer, refine_result.get("type"), queries, combined_excluded,
        )
        await _present_advise_results(
            update,
            context,
            status,
            description=description,
            queries=queries,
            user_id=user_id,
            title="Подборка по уточнению",
            show_llm_queries=False,
            progress=progress,
        )
    except Exception:
        logger.exception("_run_advise_after_clarify failed")
        await finish_status(status, update, "Ошибка при подборе.", parse_mode=None)
    finally:
        end_request_llm_budget()

_ADVISE_TRIGGERS = (
    # Желания
    "хочу", "хочется", "хотелось бы", "хотим",
    # Просьбы
    "посоветуй", "посоветуйте", "подбери", "подберите",
    "подскажи", "подскажите", "порекомендуй", "порекомендуйте",
    "предложи", "предложите",
    # Неопределённость
    "что-то", "чего-нибудь", "что нибудь", "что попробовать",
    "что взять", "что купить", "что выбрать",
    # Предпочтения
    "нравится", "нравятся", "нравилось",
    "люблю", "любим", "любят",
    "надоело", "надоела", "надоели", "скучно",
    # Контекст кальяна
    "покурить", "покурим", "покурить", "скурить",
    "кальян", "набить", "засыпать",
    "миксовать", "смешать", "сочетание", "сочетать",
    # Описательные вкусы (без конкретного запроса)
    "без мяты", "без ментола", "без льда",
    "с мятой", "с холодком", "с ментолом",
    "лёгкое", "лёгкий", "лёгкий табак",
    "крепкое", "крепкий",
    "сладкое", "сладкий", "послаще",
    "кислое", "кислый", "покислее",
    "свежее", "свежий",
    "фруктовое", "фруктовый", "ягодное", "ягодный",
    "цитрусовое", "цитрусовый", "тропическое",
    "лёгкий дым", "густой дым",
    # Образные/настроенческие запросы
    "в стакане", "настроение", "атмосфер", "на вечер",
    "для компании", "для расслабления", "для вечеринки",
    "ощущение", "вайб",
    # Интерес / категория
    "интересует", "интересуют", "интересно попробовать",
    "хочется попробовать", "хочу попробовать",
)

_MIX_TRIGGERS = (
    "микс", "миксовать", "смешать", "сочетание", "сочетать",
    "2 вкуса", "два вкуса", "несколько вкусов", "набор",
    "скомбинировать", "комбо", "компоненты",
    # Глаголы-приказы
    "собери", "подбери", "составь", "приготовь", "сделай",
    "скомпонуй", "подберёт", "собери мне", "придумай",
    # Описательные фразы
    "из нескольких", "сочетание вкусов", "несколько табаков",
    "рецепт", "смесь",
)

def _looks_like_advise(text: str) -> bool:
    """Возвращает True если текст похож на описательный запрос, а не точный вкус."""
    t = text.lower().strip()
    return any(t.startswith(w) or f" {w}" in t for w in _ADVISE_TRIGGERS)

_FRESH_REQUEST_OPENERS = (
    "хочу", "хочется", "хотел", "хотела", "хотим",
    "посоветуй", "посоветуйте",
    "подбери", "подберите",
    "предложи", "предложите",
    "порекомендуй", "порекомендуйте",
)

def _is_fresh_advise_request(text: str) -> bool:
    """Возвращает True если текст начинается с явного intent-opener и достаточно длинный.

    Используется чтобы в MODE_ADVISE_CLARIFY отличить ответ на уточнение
    («ягодное без мяты») от нового запроса («хочу что-то ягодное и лёгкое»).
    """
    t = text.lower().strip()
    if len(t.split()) < 3:
        return False
    return any(t.startswith(op + " ") or t == op for op in _FRESH_REQUEST_OPENERS)

def _looks_like_mix(text: str) -> bool:
    """Возвращает True если пользователь хочет микс/сочетание нескольких вкусов."""
    t = text.lower().strip()
    return any(w in t for w in _MIX_TRIGGERS)
