"""Кнопки главного меню (Reply Keyboard)."""

from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup

BTN_SEARCH = "🔍 Поиск по вкусу"
BTN_SINGLE = "📦 Одна позиция"
BTN_LIST = "📝 Список позиций"
BTN_HELP = "❓ Справка"
BTN_CANCEL = "↩️ Отмена"

MENU_BUTTONS = frozenset({BTN_SEARCH, BTN_SINGLE, BTN_LIST, BTN_HELP, BTN_CANCEL})


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(BTN_SEARCH)],
            [KeyboardButton(BTN_SINGLE), KeyboardButton(BTN_LIST)],
            [KeyboardButton(BTN_HELP), KeyboardButton(BTN_CANCEL)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )
