"""Режим ожидания ввода после кнопки или команды."""

from __future__ import annotations

from telegram.ext import ContextTypes

MODE_KEY = "await_mode"

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
    "🔍 <b>Поиск по вкусу</b>\n\n"
    "Напишите вкус и граммовку одним сообщением, например:\n"
    "• <code>малина 200</code>\n"
    "• <code>арбуз дыня</code>\n"
    "• <code>кокос | must have</code> — с фильтром по бренду\n\n"
    "Отмена — кнопка «↩️ Отмена»."
)

PROMPT_SINGLE = (
    "📦 <b>Проверка одной позиции</b>\n\n"
    "Отправьте <b>одну</b> строку с названием, например:\n"
    "• <code>66 мармелад кола 200</code>\n"
    "• <code>сарма малина 200</code>\n\n"
    "Отмена — кнопка «↩️ Отмена»."
)

PROMPT_LIST = (
    "📝 <b>Проверка списка</b>\n\n"
    "Отправьте несколько строк — каждая позиция с новой строки, "
    "минимум 2. Пример:\n"
    "<code>66 мармелад кола 200\n"
    "сарма малина 200\n"
    "арбуз-дыня 200</code>\n\n"
    "Отмена — кнопка «↩️ Отмена»."
)

PROMPT_CART_SINGLE = (
    "🛒 <b>В корзину — одна позиция</b>\n\n"
    "Отправьте строку как при проверке, например:\n"
    "• <code>66 мармелад кола 200</code>\n"
    "• <code>сарма малина 200 3х</code> — три упаковки\n\n"
    "Добавляется лучшее совпадение с сайта (нужен вход в Oshisha).\n"
    "Отмена — «↩️ Отмена»."
)

PROMPT_CART_LIST = (
    "🛒 <b>В корзину — список</b>\n\n"
    "Несколько строк в одном сообщении (минимум 2), "
    "как для проверки списка.\n\n"
    "Отмена — «↩️ Отмена»."
)

PROMPT_IDLE = (
    "Выберите действие кнопкой внизу или командой:\n"
    "• <code>/search</code> — поиск по вкусу\n"
    "• <code>/check</code> / <code>/list</code> — проверка\n"
    "• <code>/cart</code> / <code>/cartlist</code> — в корзину\n"
    "• <code>/cartview</code> — корзина на сайте\n"
    "• <code>/cartlog</code> — журнал добавлений"
)
