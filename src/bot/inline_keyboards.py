"""Inline-кнопки под сообщениями сценариев."""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit

CB_CANCEL = "sc:cancel"
CB_SEARCH_AGAIN = "sc:search"
CB_DISMISS = "sc:dismiss"
CB_FLAVOR_PICK = "sc:fp:"
CB_FLAVOR_CONFIRM = "sc:fc:"
CB_CHECK_PICK = "sc:cp:"
CB_CHECK_CONFIRM = "sc:cc:"
CB_BACK_FLAVOR = "sc:bf"
CB_BACK_CHECK = "sc:bc"

MAX_CART_BUTTONS = 12
BUTTONS_PER_ROW = 4


def inline_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Отмена", callback_data=CB_CANCEL)]]
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


def flavor_search_keyboard(hits: list[FlavorSearchHit]) -> InlineKeyboardMarkup | None:
    """Выбор варианта из списка (шаг перед корзиной)."""
    in_stock = _in_stock_flavor_indices(hits)
    pick_buttons: list[InlineKeyboardButton] = []
    for i in in_stock[:MAX_CART_BUTTONS]:
        label = f"Выбрать {i + 1}" if len(in_stock) > 1 else "🛒 В корзину"
        pick_buttons.append(
            InlineKeyboardButton(label, callback_data=f"{CB_FLAVOR_PICK}{i}")
        )
    if not pick_buttons:
        rows = [
            [InlineKeyboardButton("🔍 Новый поиск", callback_data=CB_SEARCH_AGAIN)],
            [InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)],
        ]
        return InlineKeyboardMarkup(rows)

    rows = _chunk_buttons(pick_buttons)
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
    rows.append([InlineKeyboardButton("❌ Закрыть", callback_data=CB_DISMISS)])
    return InlineKeyboardMarkup(rows)


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
