"""Кнопки главного меню (Reply Keyboard)."""

from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup

BTN_SEARCH = "🔍 Поиск по вкусу"
BTN_SINGLE = "📦 Одна позиция"
BTN_LIST = "📝 Список позиций"
BTN_CART = "🛒 В корзину"
BTN_CART_LIST = "🛒 Список в корзину"
BTN_VIEW_CART = "👀 Корзина"
BTN_CART_LOG = "📜 Журнал"
BTN_HELP = "❓ Справка"
BTN_CANCEL = "↩️ Отмена"

MENU_BUTTONS = frozenset(
    {
        BTN_SEARCH,
        BTN_SINGLE,
        BTN_LIST,
        BTN_CART,
        BTN_CART_LIST,
        BTN_VIEW_CART,
        BTN_CART_LOG,
        BTN_HELP,
        BTN_CANCEL,
    }
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SEARCH)],
            [KeyboardButton(BTN_SINGLE), KeyboardButton(BTN_LIST)],
            [KeyboardButton(BTN_CART), KeyboardButton(BTN_CART_LIST)],
            [KeyboardButton(BTN_VIEW_CART), KeyboardButton(BTN_CART_LOG)],
            [KeyboardButton(BTN_HELP), KeyboardButton(BTN_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
