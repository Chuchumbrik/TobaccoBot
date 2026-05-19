"""Кнопки главного меню (Reply Keyboard)."""

from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup

BTN_SEARCH = "🔍 Поиск по вкусу"
BTN_CHECK = "📦 Проверить"
BTN_CHECK_LIST = "📝 Список"
BTN_CART = "🛒 Добавить"
BTN_CART_LIST = "🛒 Список в корзину"
BTN_VIEW_CART = "👀 Корзина"
BTN_CART_LOG = "📜 Журнал"
BTN_LOG_RESET = "🔄 Новый заказ"
BTN_MENU = "🏠 Меню"
BTN_HELP = "❓ Справка"
BTN_CANCEL = "❌ Отмена"

# Обратная совместимость (если где-то остались старые названия)
BTN_SINGLE = BTN_CHECK
BTN_LIST = BTN_CHECK_LIST

MENU_BUTTONS = frozenset(
    {
        BTN_SEARCH,
        BTN_CHECK,
        BTN_CHECK_LIST,
        BTN_CART,
        BTN_CART_LIST,
        BTN_VIEW_CART,
        BTN_CART_LOG,
        BTN_LOG_RESET,
        BTN_MENU,
        BTN_HELP,
        BTN_CANCEL,
    }
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SEARCH)],
            [KeyboardButton(BTN_CHECK), KeyboardButton(BTN_CHECK_LIST)],
            [KeyboardButton(BTN_CART), KeyboardButton(BTN_CART_LIST)],
            [KeyboardButton(BTN_VIEW_CART), KeyboardButton(BTN_CART_LOG)],
            [KeyboardButton(BTN_LOG_RESET)],
            [KeyboardButton(BTN_MENU), KeyboardButton(BTN_HELP)],
            [KeyboardButton(BTN_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
