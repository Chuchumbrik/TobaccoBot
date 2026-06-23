"""Обработчики Telegram-бота (пакет)."""

from bot.handlers.common import (
    COMPARE_AVAILABLE_KEY,
    CONFIG_KEY,
    MENU_BUTTONS_KEY,
    SERVICE_KEY,
    get_config,
    get_service,
    get_shop_hub,
    is_compare_enabled,
)
from bot.handlers.admin import cmd_apply_vocab_patch, cmd_digest, cmd_reload_prompts, cmd_update_taxonomy
from bot.handlers.advise import cmd_advise
from bot.handlers.theme import cmd_theme
from bot.handlers.cart_flow import (
    cmd_cart,
    cmd_cartlist,
    cmd_cartlog,
    cmd_cartview,
    cmd_logreset,
)
from bot.handlers.check_flow import cmd_check, cmd_list
from bot.handlers.common import cmd_help, cmd_menu, cmd_start
from bot.handlers.compare import cmd_compare
from bot.handlers.flavor import cmd_search, handle_cyrillic_search_command
from bot.handlers.callbacks import handle_callback_query
from bot.handlers.routing import handle_menu_button, handle_text_message

__all__ = [
    "COMPARE_AVAILABLE_KEY",
    "MENU_BUTTONS_KEY",
    "CONFIG_KEY",
    "SERVICE_KEY",
    "get_config",
    "get_service",
    "get_shop_hub",
    "is_compare_enabled",
    "cmd_start",
    "cmd_menu",
    "cmd_help",
    "cmd_search",
    "cmd_advise",
    "cmd_check",
    "cmd_list",
    "cmd_compare",
    "cmd_cart",
    "cmd_cartlist",
    "cmd_cartview",
    "cmd_cartlog",
    "cmd_logreset",
    "cmd_apply_vocab_patch",
    "cmd_digest",
    "cmd_reload_prompts",
    "cmd_update_taxonomy",
    "cmd_theme",
    "handle_cyrillic_search_command",
    "handle_menu_button",
    "handle_text_message",
    "handle_callback_query",
]
