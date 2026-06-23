"""Inline-кнопки под сообщениями сценариев."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit
from bot.weight_groups import FlavorGroup, group_hits

CB_CANCEL = "sc:cancel"
CB_SEARCH_AGAIN = "sc:search"
CB_DISMISS = "sc:dismiss"
CB_VIEW_CART = "sc:vc"
CB_FLAVOR_PICK = "sc:fp:"
CB_FLAVOR_CONFIRM = "sc:fc:"
CB_FLAVOR_GROUP = "sc:fgrp:"     # + group_index — выбор граммовки для группы
CB_CHECK_PICK = "sc:cp:"
CB_CHECK_CONFIRM = "sc:cc:"
CB_CHECK_ALL = "sc:call"         # Добавить все позиции в наличии из проверки списка
CB_CHECK_MIN_WEIGHT = "sc:cmin"  # Добавить все по минимальной граммовке
CB_CHECK_MAX_WEIGHT = "sc:cmax"  # Добавить все по максимальной граммовке
CB_BACK_FLAVOR = "sc:bf"
CB_BACK_CHECK = "sc:bc"
CB_ADVISE_REFINE = "sc:advref"   # Уточнить рекомендацию советника
CB_ADVISE_RESET  = "sc:advr"    # Сбросить запрос советника → новый
CB_ADVISE_PAGE   = "sc:ap:"     # + page_number — пагинация советника

ADVISE_PAGE_SIZE = 9            # позиций на одной странице советника
CB_MIX_BUILD = "sc:mb:"          # + mix_index — собрать конкретный микс в корзину
CB_FLAVOR_GEN_MIX = "sc:fgm"    # Сгенерировать миксы из текущего поиска по вкусу
CB_FLAVOR_SUGGEST = "sc:fsug"   # LLM: альтернативные запросы при пустом поиске
CB_CLARIFY_RESET = "sc:clr"      # Отменить уточнение → начать новый запрос
CB_EXCL_RESET = "sc:er"          # Сбросить активные исключения (без мяты и т.п.)
CB_THEME_PAGE = "sc:thp:"        # + page_number — пагинация тематического поиска

THEME_PAGE_SIZE = 4              # вкусовых групп на одной странице темы

# Кнопки быстрого меню под приветствием
CB_WM_ADVISE  = "wm:adv"
CB_WM_SEARCH  = "wm:srch"
CB_WM_CHECK   = "wm:chk"
CB_WM_LIST    = "wm:lst"
CB_WM_CART    = "wm:crt"
CB_WM_LOG     = "wm:log"
CB_WM_COMPARE = "wm:cmp"

MAX_CART_BUTTONS = 12
BUTTONS_PER_ROW = 4


def welcome_keyboard(*, compare: bool = False) -> InlineKeyboardMarkup:
    """Быстрое меню под приветственным сообщением."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton("🎯 Советник", callback_data=CB_WM_ADVISE),
            InlineKeyboardButton("🔍 Поиск",    callback_data=CB_WM_SEARCH),
        ],
        [
            InlineKeyboardButton("📦 Проверить", callback_data=CB_WM_CHECK),
            InlineKeyboardButton("📝 Список",    callback_data=CB_WM_LIST),
        ],
        [
            InlineKeyboardButton("👀 Корзина", callback_data=CB_WM_CART),
            InlineKeyboardButton("📜 Журнал",  callback_data=CB_WM_LOG),
        ],
    ]
    if compare:
        rows.append([InlineKeyboardButton("⚖️ Сравнить", callback_data=CB_WM_COMPARE)])
    return InlineKeyboardMarkup(rows)


def inline_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data=CB_CANCEL)]]
    )


def clarify_question_keyboard() -> InlineKeyboardMarkup:
    """Кнопки под уточняющим вопросом: сбросить или отменить."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔄 Новый запрос", callback_data=CB_CLARIFY_RESET)],
            [InlineKeyboardButton("❌ Отмена", callback_data=CB_CANCEL)],
        ]
    )


def after_cart_keyboard() -> InlineKeyboardMarkup:
    """Кнопки после успешного добавления в корзину."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔍 Ещё поиск", callback_data=CB_SEARCH_AGAIN),
                InlineKeyboardButton("👀 Корзина", callback_data=CB_VIEW_CART),
            ]
        ]
    )


def _chunk_buttons(
    buttons: list[InlineKeyboardButton],
    *,
    per_row: int = BUTTONS_PER_ROW,
) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for btn in buttons:
        row.append(btn)
        if len(row) >= per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _in_stock_flavor_indices(hits: list[FlavorSearchHit]) -> list[int]:
    return [i for i, h in enumerate(hits) if h.status == "есть"]


def _in_stock_check_indices(results: list[ProductCheckResult]) -> list[int]:
    return [i for i, r in enumerate(results) if r.status == "есть"]


def _excl_reset_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton("🚫 Сбросить фильтры", callback_data=CB_EXCL_RESET)]


def _flavor_pick_buttons(hits: list[FlavorSearchHit]) -> list[InlineKeyboardButton]:
    """Строит кнопки выбора с учётом группировки по граммовкам.

    Группы с одним вариантом в наличии → CB_FLAVOR_PICK:{hit_index}.
    Группы с несколькими вариантами в наличии → CB_FLAVOR_GROUP:{group_index}.
    Метка «Выбрать N» соответствует номеру группы в отображаемом списке.
    """
    groups = group_hits(hits)
    in_stock_groups = [(gi, g) for gi, g in enumerate(groups) if g.status == "есть"]
    pick_buttons: list[InlineKeyboardButton] = []
    for gi, group in in_stock_groups[:MAX_CART_BUTTONS]:
        display_number = gi + 1
        label = f"Выбрать {display_number}" if len(in_stock_groups) > 1 else "🛒 В корзину"
        in_stock_v = group.in_stock_variants
        if len(in_stock_v) > 1:
            callback = f"{CB_FLAVOR_GROUP}{gi}"
        else:
            callback = f"{CB_FLAVOR_PICK}{in_stock_v[0].hit_index}"
        pick_buttons.append(InlineKeyboardButton(label, callback_data=callback))
    return pick_buttons


def flavor_search_keyboard(
    hits: list[FlavorSearchHit],
    *,
    excluded: list[str] | None = None,
) -> InlineKeyboardMarkup | None:
    """Выбор варианта из списка (шаг перед корзиной)."""
    pick_buttons = _flavor_pick_buttons(hits)
    if not pick_buttons:
        rows = flavor_empty_extra_rows()
        if excluded:
            rows.insert(0, _excl_reset_row())
        return InlineKeyboardMarkup(rows)

    rows = _chunk_buttons(pick_buttons)
    if excluded:
        rows.append(_excl_reset_row())
    rows.append([InlineKeyboardButton("🔍 Новый поиск", callback_data=CB_SEARCH_AGAIN)])
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)])
    return InlineKeyboardMarkup(rows)


def flavor_empty_extra_rows() -> list[list[InlineKeyboardButton]]:
    return [
        [InlineKeyboardButton("💡 Подобрать альтернативы", callback_data=CB_FLAVOR_SUGGEST)],
        [InlineKeyboardButton("🔍 Новый поиск", callback_data=CB_SEARCH_AGAIN)],
        [InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)],
    ]


def flavor_search_keyboard_with_mix(
    hits: list[FlavorSearchHit],
    *,
    excluded: list[str] | None = None,
) -> InlineKeyboardMarkup | None:
    """Как flavor_search_keyboard, но с кнопкой «🎲 Сгенерировать микс»."""
    pick_buttons = _flavor_pick_buttons(hits)

    if not pick_buttons:
        rows = [
            [InlineKeyboardButton("🎲 Сгенерировать микс", callback_data=CB_FLAVOR_GEN_MIX)],
            *flavor_empty_extra_rows(),
        ]
        if excluded:
            rows.insert(0, _excl_reset_row())
        return InlineKeyboardMarkup(rows)

    rows = _chunk_buttons(pick_buttons)
    rows.append([InlineKeyboardButton("🎲 Сгенерировать микс", callback_data=CB_FLAVOR_GEN_MIX)])
    if excluded:
        rows.append(_excl_reset_row())
    rows.append([InlineKeyboardButton("🔍 Новый поиск", callback_data=CB_SEARCH_AGAIN)])
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)])
    return InlineKeyboardMarkup(rows)


def flavor_confirm_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 Положить в корзину",
                    callback_data=f"{CB_FLAVOR_CONFIRM}{index}",
                )
            ],
            [InlineKeyboardButton("◀️ Другой вариант", callback_data=CB_BACK_FLAVOR)],
            [InlineKeyboardButton("❌ Отмена", callback_data=CB_DISMISS)],
        ]
    )


def _has_multi_weight_variants(results: list[ProductCheckResult]) -> bool:
    """True если хотя бы один результат имеет ≥2 варианта граммовки."""
    return any(len(r.weight_variants) >= 2 for r in results if r.status != "не найден")


def check_results_keyboard(results: list[ProductCheckResult]) -> InlineKeyboardMarkup | None:
    """Выбор позиции из проверки (шаг перед корзиной)."""
    in_stock = _in_stock_check_indices(results)
    pick_buttons: list[InlineKeyboardButton] = []
    for i in in_stock[:MAX_CART_BUTTONS]:
        label = f"Выбрать {i + 1}" if len(in_stock) > 1 else "🛒 В корзину"
        pick_buttons.append(
            InlineKeyboardButton(label, callback_data=f"{CB_CHECK_PICK}{i}")
        )
    if not pick_buttons:
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)]]
        )
    rows = _chunk_buttons(pick_buttons)
    if len(in_stock) > 1:
        rows.append([
            InlineKeyboardButton(
                f"🛒 Добавить всё в наличии ({len(in_stock)})",
                callback_data=CB_CHECK_ALL,
            )
        ])
    if _has_multi_weight_variants(results):
        rows.append([
            InlineKeyboardButton("📦 Все мин. вес", callback_data=CB_CHECK_MIN_WEIGHT),
            InlineKeyboardButton("📦 Все макс. вес", callback_data=CB_CHECK_MAX_WEIGHT),
        ])
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)])
    return InlineKeyboardMarkup(rows)


def advise_keyboard(
    hits: list[FlavorSearchHit],
    *,
    excluded: list[str] | None = None,
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Клавиатура советника: выбор с группировкой граммовок + постраничная навигация."""
    all_groups = group_hits(hits)
    total = len(all_groups)
    total_pages = max(1, (total + ADVISE_PAGE_SIZE - 1) // ADVISE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * ADVISE_PAGE_SIZE

    # Pick-кнопки только для текущей страницы, с абсолютными индексами групп
    pick_buttons: list[InlineKeyboardButton] = []
    total_in_stock = sum(1 for g in all_groups if g.status == "есть")
    for rel_i, group in enumerate(all_groups[start:start + ADVISE_PAGE_SIZE]):
        abs_gi = start + rel_i
        if group.status != "есть":
            continue
        display_number = abs_gi + 1
        label = f"Выбрать {display_number}" if total_in_stock > 1 else "🛒 В корзину"
        in_stock_v = group.in_stock_variants
        if len(in_stock_v) > 1:
            callback = f"{CB_FLAVOR_GROUP}{abs_gi}"
        else:
            callback = f"{CB_FLAVOR_PICK}{in_stock_v[0].hit_index}"
        pick_buttons.append(InlineKeyboardButton(label, callback_data=callback))

    rows = _chunk_buttons(pick_buttons, per_row=3) if pick_buttons else []

    # Навигационная строка (только при нескольких страницах)
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"{CB_ADVISE_PAGE}{page - 1}"))
        nav.append(InlineKeyboardButton(f"{page + 1} / {total_pages}", callback_data="sc:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"{CB_ADVISE_PAGE}{page + 1}"))
        rows.append(nav)

    if excluded:
        rows.append(_excl_reset_row())
    rows.append([InlineKeyboardButton("🔄 Сбросить запрос", callback_data=CB_ADVISE_RESET)])
    return InlineKeyboardMarkup(rows)


def mix_results_keyboard(recipes: list[dict]) -> InlineKeyboardMarkup:
    """
    Кнопки для рецептов миксов.
    recipes = [{"name": str, "indices": [int, ...]}, ...]
    Показывает кнопку «🛒 Собрать» только для рецептов с найденными позициями.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for i, recipe in enumerate(recipes):
        name = recipe.get("name", f"Микс {i + 1}")
        if recipe.get("indices"):  # есть в наличии компоненты
            rows.append([
                InlineKeyboardButton(
                    f"🛒 Собрать «{name}»",
                    callback_data=f"{CB_MIX_BUILD}{i}",
                )
            ])
    rows.append([InlineKeyboardButton("🔄 Сбросить запрос", callback_data=CB_ADVISE_RESET)])
    return InlineKeyboardMarkup(rows)


def weight_picker_keyboard(group: FlavorGroup) -> InlineKeyboardMarkup:
    """Кнопки выбора граммовки для группы вариантов.

    Каждая кнопка запускает стандартный флоу подтверждения через CB_FLAVOR_PICK.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for v in group.in_stock_variants:
        weight_label = f"{v.weight_g} гр" if v.weight_g else v.hit.product.name
        price_part = (
            f" — {int(v.hit.product.price)} ₽"
            if v.hit.product.price is not None
            else ""
        )
        rows.append([
            InlineKeyboardButton(
                f"🛒 {weight_label}{price_part}",
                callback_data=f"{CB_FLAVOR_PICK}{v.hit_index}",
            )
        ])
    rows.append([InlineKeyboardButton("◀️ Назад к списку", callback_data=CB_BACK_FLAVOR)])
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data=CB_DISMISS)])
    return InlineKeyboardMarkup(rows)


def theme_keyboard(
    term_hits: dict,
    *,
    page: int = 0,
) -> InlineKeyboardMarkup:
    """Клавиатура тематического поиска: навигация по страницам групп."""
    non_empty = [(t, h) for t, h in term_hits.items() if h]
    total = len(non_empty)
    total_pages = max(1, (total + THEME_PAGE_SIZE - 1) // THEME_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    rows: list[list[InlineKeyboardButton]] = []

    # Навигационная строка (только при нескольких страницах)
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(InlineKeyboardButton("◀️", callback_data=f"{CB_THEME_PAGE}{page - 1}"))
        nav.append(InlineKeyboardButton(
            f"{page + 1} / {total_pages}", callback_data="sc:noop"
        ))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("▶️", callback_data=f"{CB_THEME_PAGE}{page + 1}"))
        rows.append(nav)

    rows.append([InlineKeyboardButton("🔍 Поиск по вкусу", callback_data=CB_SEARCH_AGAIN)])
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)])
    return InlineKeyboardMarkup(rows)


_CHAT_ACTION_LABELS: dict[str, tuple[str, str]] = {
    "advise": ("🎯 Подобрать из каталога", CB_WM_ADVISE),
    "search": ("🔍 Найти в каталоге",      CB_WM_SEARCH),
    "check":  ("📦 Проверить наличие",     CB_WM_CHECK),
}


def chat_action_keyboard(action: str | None) -> InlineKeyboardMarkup | None:
    """Кнопка под chat-ответом — предлагает запустить функцию бота."""
    if not action:
        return None
    btn = _CHAT_ACTION_LABELS.get(action)
    if not btn:
        return None
    label, cb = btn
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=cb)]])


def check_confirm_keyboard(index: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🛒 Положить в корзину",
                    callback_data=f"{CB_CHECK_CONFIRM}{index}",
                )
            ],
            [InlineKeyboardButton("◀️ Другой вариант", callback_data=CB_BACK_CHECK)],
            [InlineKeyboardButton("❌ Отмена", callback_data=CB_DISMISS)],
        ]
    )
