"""Режим ожидания ввода после кнопки или команды."""

from __future__ import annotations

from telegram.ext import ContextTypes

MODE_KEY = "await_mode"

PROMPT_FOOTER = "\n\n<i>❌ Отмена · 🏠 Меню · /help</i>"

MODE_FLAVOR = "flavor"
MODE_SINGLE = "single"
MODE_LIST = "list"
MODE_CART_SINGLE = "cart_single"
MODE_CART_LIST = "cart_list"


def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    context.user_data[MODE_KEY] = mode


def get_mode(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    return context.user_data.get(MODE_KEY)


def clear_mode(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(MODE_KEY, None)


def has_mode(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return MODE_KEY in context.user_data


PROMPT_FLAVOR = (
    "🔍 <b>Шаг 2 из 2 — поиск по вкусу</b>\n\n"
    "Отправьте <b>одну строку</b> с вкусом и граммовкой:\n"
    "• <code>малина 200</code>\n"
    "• <code>арбуз дыня</code>\n"
    "• <code>кокос | must have</code>\n\n"
    "Или сразу: <code>/search малина 200</code>"
    + PROMPT_FOOTER
)

PROMPT_SINGLE = (
    "📦 <b>Шаг 2 из 2 — проверка позиции</b>\n\n"
    "Отправьте <b>одну</b> строку из прайса:\n"
    "• <code>66 мармелад кола 200</code>\n"
    "• <code>сарма малина 200</code>\n"
    "• <code>бб черешня 200 3х</code>\n\n"
    "Или: <code>/check ваша строка</code>"
    + PROMPT_FOOTER
)

PROMPT_LIST = (
    "📝 <b>Шаг 2 из 2 — проверка списка</b>\n\n"
    "Одно сообщение, <b>каждая позиция с новой строки</b> (минимум 2).\n"
    "Пример:\n"
    "<code>66 мармелад кола 200\n"
    "сарма малина 200\n"
    "арбуз-дыня 200</code>\n\n"
    "Команда: /list"
    + PROMPT_FOOTER
)

PROMPT_CART_SINGLE = (
    "🛒 <b>Шаг 2 из 2 — в корзину</b>\n\n"
    "Та же строка, что для проверки:\n"
    "• <code>66 мармелад кола 200</code>\n"
    "• <code>сарма малина 200 3х</code> — три упаковки\n\n"
    "Или: <code>/cart ваша строка</code>"
    + PROMPT_FOOTER
)

PROMPT_CART_LIST = (
    "🛒 <b>Шаг 2 из 2 — список в корзину</b>\n\n"
    "Несколько строк в одном сообщении (минимум 2), "
    "как при проверке списка.\n\n"
    "Команда: /cartlist"
    + PROMPT_FOOTER
)

PROMPT_IDLE = (
    "👋 Выберите действие <b>кнопкой внизу</b>.\n\n"
    "Не знаете с чего начать → <b>🔍 Поиск по вкусу</b>\n"
    "Есть строка из прайса → <b>📦 Проверить</b>\n"
    "Полная инструкция → <b>❓ Справка</b> или /help"
)
