"""Контекст последних результатов для inline-действий."""

from __future__ import annotations

from telegram.ext import ContextTypes

from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

KEY_FLAVOR_HITS = "sc_fh"
KEY_FLAVOR_QUERY = "sc_fq"
KEY_CHECKS = "sc_ch"
KEY_PICK_MSG_ID = "sc_pick_msg"
KEY_ADVISE_DESC    = "sc_adv_desc"   # исходное описание для режима советника
KEY_CLARIFY_ORIG   = "sc_clar_orig"  # оригинальный запрос до уточнения
KEY_CLARIFY_IS_MIX = "sc_clar_mix"   # флаг: это был микс-запрос
KEY_WAS_MIX        = "sc_was_mix"    # флаг: последний результат был миксом
KEY_MIX_RECIPES    = "sc_mixes"      # рецепты миксов: [{"name": str, "indices": [int]}]
KEY_EXCLUSIONS     = "sc_excl"       # активные исключения: ["Адалия", "манго", ...]
KEY_FLAVOR_NORM    = "sc_fnorm"      # нормализованный запрос (для suggest)
KEY_FLAVOR_PARSED  = "sc_fparsed"    # summary parse_query для suggest
KEY_THEME_DATA     = "sc_theme"      # {"term_hits": dict, "theme": str, "theme_display": str}


def save_flavor_search(
    context: ContextTypes.DEFAULT_TYPE,
    result: FlavorSearchResult,
) -> None:
    context.user_data[KEY_FLAVOR_QUERY] = result.query
    context.user_data[KEY_FLAVOR_HITS] = list(result.hits)


def save_flavor_suggest_meta(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    normalized: str,
    parsed_summary: str,
) -> None:
    context.user_data[KEY_FLAVOR_NORM] = normalized
    context.user_data[KEY_FLAVOR_PARSED] = parsed_summary


def get_flavor_suggest_meta(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, str]:
    return (
        context.user_data.get(KEY_FLAVOR_NORM, ""),
        context.user_data.get(KEY_FLAVOR_PARSED, ""),
    )


def get_flavor_hits(context: ContextTypes.DEFAULT_TYPE) -> list[FlavorSearchHit]:
    return context.user_data.get(KEY_FLAVOR_HITS) or []


def get_flavor_query(context: ContextTypes.DEFAULT_TYPE) -> str:
    """Возвращает последний поисковый запрос по вкусу."""
    return context.user_data.get(KEY_FLAVOR_QUERY, "")


def save_checks(
    context: ContextTypes.DEFAULT_TYPE,
    results: list[ProductCheckResult],
) -> None:
    context.user_data[KEY_CHECKS] = list(results)


def get_checks(context: ContextTypes.DEFAULT_TYPE) -> list[ProductCheckResult]:
    return context.user_data.get(KEY_CHECKS) or []


def set_pick_message_id(context: ContextTypes.DEFAULT_TYPE, message_id: int) -> None:
    context.user_data[KEY_PICK_MSG_ID] = message_id


def get_pick_message_id(context: ContextTypes.DEFAULT_TYPE) -> int | None:
    return context.user_data.get(KEY_PICK_MSG_ID)


def clear_pick_message_id(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY_PICK_MSG_ID, None)


def save_advise_description(context: ContextTypes.DEFAULT_TYPE, description: str) -> None:
    context.user_data[KEY_ADVISE_DESC] = description


def get_advise_description(context: ContextTypes.DEFAULT_TYPE) -> str:
    return context.user_data.get(KEY_ADVISE_DESC, "")


def save_clarify_state(context: ContextTypes.DEFAULT_TYPE, original: str, is_mix: bool = False) -> None:
    context.user_data[KEY_CLARIFY_ORIG]   = original
    context.user_data[KEY_CLARIFY_IS_MIX] = is_mix


def get_clarify_state(context: ContextTypes.DEFAULT_TYPE) -> tuple[str, bool]:
    """Возвращает (original_description, is_mix)."""
    return (
        context.user_data.get(KEY_CLARIFY_ORIG, ""),
        context.user_data.get(KEY_CLARIFY_IS_MIX, False),
    )


def save_was_mix(context: ContextTypes.DEFAULT_TYPE, is_mix: bool) -> None:
    """Запоминает, был ли последний результат советника миксом."""
    context.user_data[KEY_WAS_MIX] = is_mix


def get_was_mix(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """True если последний результат советника был набором рецептов-миксов."""
    return bool(context.user_data.get(KEY_WAS_MIX, False))


def save_mix_recipes(
    context: ContextTypes.DEFAULT_TYPE,
    recipes: list[dict],
) -> None:
    """Сохраняет рецепты миксов: [{"name": str, "indices": [int, ...]}]."""
    context.user_data[KEY_MIX_RECIPES] = list(recipes)


def get_mix_recipes(context: ContextTypes.DEFAULT_TYPE) -> list[dict]:
    """Возвращает сохранённые рецепты миксов."""
    return context.user_data.get(KEY_MIX_RECIPES) or []


def save_exclusions(context: ContextTypes.DEFAULT_TYPE, excluded: list[str]) -> None:
    """Сохраняет активные исключения для текущей сессии поиска."""
    context.user_data[KEY_EXCLUSIONS] = list(excluded)


def get_exclusions(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Возвращает активные исключения."""
    return context.user_data.get(KEY_EXCLUSIONS) or []


def clear_advise_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Сбрасывает только состояние советника — для чистого нового запроса.

    Не трогает KEY_FLAVOR_HITS/KEY_CHECKS — они от /search и /check,
    а не от советника.
    """
    context.user_data.pop(KEY_ADVISE_DESC, None)
    context.user_data.pop(KEY_CLARIFY_ORIG, None)
    context.user_data.pop(KEY_CLARIFY_IS_MIX, None)
    context.user_data.pop(KEY_WAS_MIX, None)
    context.user_data.pop(KEY_MIX_RECIPES, None)
    context.user_data.pop(KEY_EXCLUSIONS, None)


def save_theme_search(
    context: ContextTypes.DEFAULT_TYPE,
    term_hits: dict,
    theme: str,
    theme_display: str,
) -> None:
    """Сохраняет результаты тематического поиска для пагинации."""
    context.user_data[KEY_THEME_DATA] = {
        "term_hits": term_hits,
        "theme": theme,
        "theme_display": theme_display,
    }


def get_theme_search(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    """Возвращает сохранённые данные тематического поиска или None."""
    return context.user_data.get(KEY_THEME_DATA)


def clear_action_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(KEY_FLAVOR_HITS, None)
    context.user_data.pop(KEY_FLAVOR_QUERY, None)
    context.user_data.pop(KEY_CHECKS, None)
    context.user_data.pop(KEY_ADVISE_DESC, None)
    context.user_data.pop(KEY_CLARIFY_ORIG, None)
    context.user_data.pop(KEY_CLARIFY_IS_MIX, None)
    context.user_data.pop(KEY_WAS_MIX, None)
    context.user_data.pop(KEY_MIX_RECIPES, None)
    context.user_data.pop(KEY_EXCLUSIONS, None)
    context.user_data.pop(KEY_FLAVOR_NORM, None)
    context.user_data.pop(KEY_FLAVOR_PARSED, None)
    context.user_data.pop(KEY_THEME_DATA, None)
    clear_pick_message_id(context)
