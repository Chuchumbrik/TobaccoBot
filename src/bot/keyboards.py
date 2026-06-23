"""Кнопки главного меню (Reply Keyboard)."""

from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup

BTN_SEARCH = "🔍 Поиск по вкусу"
BTN_ADVISE = "🎯 Советник"
BTN_CHECK = "📦 Проверить"
BTN_CHECK_LIST = "📝 Список"
BTN_VIEW_CART = "👀 Корзина"
BTN_CART_LOG = "📜 Журнал"
BTN_LOG_RESET = "🔄 Новый заказ"
BTN_MENU = "🏠 Меню"
BTN_HELP = "❓ Справка"
BTN_COMPARE = "⚖️ Сравнить"
BTN_CANCEL = "❌ Отмена"

# Обратная совместимость (если где-то остались старые названия)
BTN_SINGLE = BTN_CHECK
BTN_LIST = BTN_CHECK_LIST

_BASE_MENU_BUTTONS = frozenset(
    {
        BTN_SEARCH,
        BTN_ADVISE,
        BTN_CHECK,
        BTN_CHECK_LIST,
        BTN_VIEW_CART,
        BTN_CART_LOG,
        BTN_LOG_RESET,
        BTN_MENU,
        BTN_HELP,
        BTN_CANCEL,
    }
)

# Обратная совместимость
MENU_BUTTONS = _BASE_MENU_BUTTONS


def menu_buttons(*, compare: bool = False) -> frozenset[str]:
    if compare:
        return _BASE_MENU_BUTTONS | {BTN_COMPARE}
    return _BASE_MENU_BUTTONS


def main_menu_keyboard(*, compare: bool = False) -> ReplyKeyboardMarkup:
    row1 = [KeyboardButton(BTN_SEARCH), KeyboardButton(BTN_ADVISE)]
    if compare:
        row1.append(KeyboardButton(BTN_COMPARE))
    return ReplyKeyboardMarkup(
        [
            row1,
            [
                KeyboardButton(BTN_CHECK),
                KeyboardButton(BTN_CHECK_LIST),
                KeyboardButton(BTN_VIEW_CART),
            ],
            [KeyboardButton(BTN_CART_LOG), KeyboardButton(BTN_LOG_RESET), KeyboardButton(BTN_HELP)],
        ],
        resize_keyboard=True,
    )
