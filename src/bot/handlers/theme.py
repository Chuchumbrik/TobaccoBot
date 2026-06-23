"""Тематический поиск: «выпечка», «травянистые», «пряные» и т.п.

Отличие от /advise:
  /advise   — LLM → плоский список хитов, сортировка по релевантности
  /theme    → grouped результаты: каждая вкусовая группа — отдельный блок

Поток:
  1. Тема → список вкусовых запросов (таксономия → LLM)
  2. Параллельный поиск каждого запроса (search_terms_as_map)
  3. Вывод в виде блоков с пагинацией: «Ваниль · Корица · …» страницами по 4 группы
"""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot.handlers.common import (
    finish_status,
    get_config,
    get_service,
    reply,
    send_status,
    user_snapshot,
)
from bot.action_context import save_theme_search
from bot.catalog_search import search_terms_as_map
from bot.inline_keyboards import THEME_PAGE_SIZE, theme_keyboard
from bot.search_log import log_search
from bot.llm_gate import (
    begin_request_llm_budget,
    consume_request_llm_budget,
    end_request_llm_budget,
)
from oshisha import catalog_cache
from oshisha.flavor_search import FlavorSearchHit
from oshisha.llm import get_llm_trace, start_llm_trace, theme_to_queries
from oshisha.taxonomy import is_profile_word, resolve_component

logger = logging.getLogger(__name__)

_HITS_PER_TERM = 5   # хитов на один вкусовой запрос
_MAX_TERMS = 8       # максимум вкусовых запросов для темы

# ── Известные темы (короткие слова, которые в текстовом вводе роутируются сюда) ─

_KNOWN_THEMES: frozenset[str] = frozenset({
    # ── Выпечка ──────────────────────────────────────────────────────────────────
    "выпечка",
    # ── Травянистые / пряные ─────────────────────────────────────────────────────
    "травянистые", "травянистый", "травяной", "травяные", "травяное",
    "пряные", "пряный", "пряное", "пряная",
    # ── Фруктовые ────────────────────────────────────────────────────────────────
    "фруктовые", "фруктовый", "фруктовое", "фруктовая",
    # ── Ягодные ──────────────────────────────────────────────────────────────────
    "ягодные", "ягодный", "ягодное", "ягодная",
    # ── Цветочные ────────────────────────────────────────────────────────────────
    "цветочные", "цветочный", "цветочное",
    # ── Ореховые ─────────────────────────────────────────────────────────────────
    "ореховые", "ореховый", "ореховое",
    # ── Алкогольные ──────────────────────────────────────────────────────────────
    "алкогольные", "алкогольный", "алкогольное",
    # ── Десертные ────────────────────────────────────────────────────────────────
    "десертные", "десертный", "десертное",
    # ── Тропические ──────────────────────────────────────────────────────────────
    "тропические", "тропический", "тропическое",
    # ── Цитрусовые ───────────────────────────────────────────────────────────────
    "цитрусовые", "цитрусовый", "цитрусовое",
    # ── Ментоловые ───────────────────────────────────────────────────────────────
    "ментоловые", "ментоловый", "ментоловое",
    # ── Молочные ─────────────────────────────────────────────────────────────────
    "молочные", "молочный", "молочное",
    # ── Табачные ─────────────────────────────────────────────────────────────────
    "табачные", "табачный", "табачное",
    # ── Карамельные ──────────────────────────────────────────────────────────────
    "карамельные", "карамельный", "карамельное",
    # ── Шоколадные ───────────────────────────────────────────────────────────────
    "шоколадные", "шоколадный", "шоколадное",
    # ── Кофейные ─────────────────────────────────────────────────────────────────
    "кофейные", "кофейный", "кофейное",
    # ── Восточные ────────────────────────────────────────────────────────────────
    "восточные", "восточный", "восточное",
    # ── Газированные ─────────────────────────────────────────────────────────────
    "газированные", "газированный", "газированное",
})


def _looks_like_theme(text: str) -> bool:
    """True если текст — одно слово из явно тематических профилей.

    Проверяет только _KNOWN_THEMES, а не всю таксономию — иначе конкретные
    вкусы («малина», «лимон») ошибочно шли бы в тематический поиск.
    """
    t = text.lower().strip()
    # Только одно слово (фразы типа «хочу выпечку» → советник, а не тема)
    if " " in t:
        return False
    return t in _KNOWN_THEMES


# ── Разрешение темы в термины ─────────────────────────────────────────────────

async def _resolve_theme_terms(
    context: ContextTypes.DEFAULT_TYPE,
    theme: str,
) -> tuple[list[str], str]:
    """Конвертирует тему в список вкусовых поисковых запросов.

    Приоритеты:
      1. Таксономия (мгновенно, без LLM)
      2. LLM (если тема не в таксономии и есть бюджет)
      3. Прямой поиск (fallback)

    Возвращает (search_terms, theme_display_name).
    """
    key = theme.lower().strip()

    # ── 1. Таксономия ─────────────────────────────────────────────────────────
    if is_profile_word(key):
        terms = resolve_component(key, max_terms=_MAX_TERMS)
        # resolve_component возвращает [key] как fallback — отличаем реальные термины
        if terms and (len(terms) > 1 or terms[0] != key):
            logger.info("theme resolved via taxonomy: %r → %s", key, terms)
            return terms, theme.capitalize()

    # ── 2. LLM ────────────────────────────────────────────────────────────────
    if consume_request_llm_budget(context, 1):
        result = await theme_to_queries(theme)
        terms = result.get("terms", [])
        display = result.get("theme_display", theme.capitalize())
        if terms:
            logger.info("theme resolved via LLM: %r → %s", key, terms)
            return terms[:_MAX_TERMS], display

    # ── 3. Fallback ───────────────────────────────────────────────────────────
    return [theme], theme.capitalize()


# ── Форматирование тематических результатов ───────────────────────────────────

_AROMAT_RE = re.compile(r'^.*?\bс\s+ароматом\s+', re.IGNORECASE | re.DOTALL)


def _compact_name(product_name: str) -> str:
    """Убирает «Brand с ароматом » из начала названия."""
    m = _AROMAT_RE.match(product_name)
    return product_name[m.end():].strip() if m else product_name


def _format_theme_page(
    theme: str,
    theme_display: str,
    term_hits: dict[str, list[FlavorSearchHit]],
    *,
    page: int = 0,
    page_size: int = THEME_PAGE_SIZE,
) -> str:
    """Формирует текст одной страницы тематических результатов."""
    non_empty = [(t, h) for t, h in term_hits.items() if h]
    total_terms = len(non_empty)
    total_pages = max(1, (total_terms + page_size - 1) // page_size)
    page = max(0, min(page, total_pages - 1))

    total_hits = sum(len(hits) for _, hits in non_empty)
    total_in_stock = sum(
        1 for _, hits in non_empty for h in hits if h.status == "есть"
    )

    # Заголовок
    lines: list[str] = [
        f"🎨 <b>Тема: {theme_display}</b>",
    ]
    if total_pages > 1:
        lines.append(
            f"Найдено {total_hits} позиций, в наличии: {total_in_stock}  ·  "
            f"стр. {page + 1}/{total_pages}"
        )
    else:
        lines.append(f"Найдено {total_hits} позиций, в наличии: {total_in_stock}")
    lines.append(catalog_cache.stock_disclaimer_html())
    lines.append("")

    # Группы текущей страницы
    start = page * page_size
    page_terms = non_empty[start:start + page_size]

    for term, hits in page_terms:
        in_stock_count = sum(1 for h in hits if h.status == "есть")
        oos_count = len(hits) - in_stock_count
        term_cap = term.capitalize()

        stock_str = f"✅ {in_stock_count}" if in_stock_count else ""
        oos_str = f"❌ {oos_count}" if oos_count else ""
        count_str = "  ".join(p for p in [stock_str, oos_str] if p)
        lines.append(f"<b>{term_cap}</b>  <i>{count_str}</i>")

        for h in hits:
            icon = "✅" if h.status == "есть" else "❌"
            name = _compact_name(h.product.name)
            brand = f" <i>[{h.brand_display}]</i>" if h.brand_display else ""
            lines.append(f"  {icon} {name}{brand}")
        lines.append("")

    lines.append("<i>💡 Для точного поиска — /search &lt;вкус&gt;</i>")
    return "\n".join(lines).strip()


# ── Основной флоу ─────────────────────────────────────────────────────────────

async def _run_theme_search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    theme: str,
) -> None:
    """Тематический поиск: theme → grouped flavor results."""
    if not update.message:
        return

    config = get_config(context)
    user_id, _, _ = user_snapshot(update)
    service = get_service(context)

    status = await send_status(update, f"🎨 Ищу по теме «{theme}»…")
    start_llm_trace()
    begin_request_llm_budget(context, config=config, user_id=user_id)

    try:
        # ── Шаг 1: тема → вкусовые запросы ───────────────────────────────────
        terms, theme_display = await _resolve_theme_terms(context, theme)

        # ── Шаг 2: поиск по каждому вкусу ────────────────────────────────────
        await status.edit_text(
            f"🎨 Тема «{theme}»…\n<i>Ищу: {', '.join(terms)}</i>",
            parse_mode=ParseMode.HTML,
        )
        term_hits, catalog_calls = await search_terms_as_map(
            service,
            terms,
            limit_per_term=_HITS_PER_TERM,
        )

        # Убираем пустые группы
        term_hits = {t: hits for t, hits in term_hits.items() if hits}

        if not term_hits:
            await finish_status(
                status, update,
                f"😔 По теме «{theme}» ничего не нашлось.\n"
                "Попробуй другую тему или используй /search.",
                parse_mode=None,
            )
            return

        # ── Шаг 3: сохраняем для пагинации ───────────────────────────────────
        save_theme_search(context, term_hits, theme, theme_display)

        # ── Шаг 4: форматируем первую страницу ───────────────────────────────
        text = _format_theme_page(theme, theme_display, term_hits, page=0)
        keyboard = theme_keyboard(term_hits, page=0)

        # ── Лог ──────────────────────────────────────────────────────────────
        total_hits = sum(len(h) for h in term_hits.values())
        in_stock = sum(1 for hits in term_hits.values() for h in hits if h.status == "есть")
        trace = get_llm_trace()
        first_hits = [h.product.name for hits in list(term_hits.values())[:2] for h in hits[:2]]
        log_search(
            user_id=user_id,
            query=theme,
            intent=", ".join(terms),
            search_type="theme",
            results_count=total_hits,
            in_stock_count=in_stock,
            top_names=first_hits[:3],
            llm_backend=trace.backend if trace else None,
            llm_calls=trace.calls if trace else None,
            llm_total_ms=trace.total_ms if trace else None,
            catalog_calls=catalog_calls,
        )

        if len(text) > 4000:
            text = text[:3990] + "\n…"

        await finish_status(
            status, update, text,
            parse_mode=ParseMode.HTML,
            inline_markup=keyboard,
        )

    except Exception:
        logger.exception("_run_theme_search failed for %r", theme)
        await finish_status(status, update, "Ошибка при тематическом поиске.", parse_mode=None)
    finally:
        end_request_llm_budget()


# ── Команда /theme ────────────────────────────────────────────────────────────

async def cmd_theme(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/theme <тема> — тематический поиск по каталогу."""
    theme = " ".join(context.args).strip() if context.args else ""
    if not theme:
        await reply(
            update,
            context,
            "🎨 <b>Тематический поиск</b>\n\n"
            "Укажи тему — я найду все вкусы этой категории.\n\n"
            "<i>Примеры:\n"
            "• /theme выпечка\n"
            "• /theme травянистые\n"
            "• /theme пряные\n"
            "• /theme цветочные\n"
            "• /theme ореховые\n"
            "• /theme алкогольные\n"
            "• /theme тропические\n"
            "• /theme фруктовые\n"
            "• /theme шоколадные\n"
            "• /theme восточные</i>",
            parse_mode=ParseMode.HTML,
        )
        return
    await _run_theme_search(update, context, theme)
