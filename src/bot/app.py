"""Сборка Telegram Application."""

from __future__ import annotations

import logging

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from bot.config import BotConfig, load_config
from bot.handlers import (
    CONFIG_KEY,
    cmd_help,
    cmd_search,
    cmd_start,
    handle_check_list,
    handle_cyrillic_search_command,
    handle_flavor_prefix,
    handle_single_line,
)

# Telegram: только a-z, цифры, _ (кириллица — через handle_cyrillic_search_command)
SEARCH_COMMANDS = ["search", "poisk", "vkus", "flavor", "v"]

logger = logging.getLogger(__name__)


async def _setup_bot_commands(application: Application) -> None:
    """Меню команд в Telegram (кнопка «/» у поля ввода)."""
    await application.bot.set_my_commands(
        [
            BotCommand("search", "Поиск по вкусу — например: малина 200"),
            BotCommand("poisk", "То же, что /search"),
            BotCommand("start", "Справка и примеры"),
            BotCommand("help", "Справка"),
        ]
    )


def _application_builder(config: BotConfig):
    builder = (
        Application.builder()
        .token(config.telegram_token)
        .post_init(_setup_bot_commands)
        .connect_timeout(config.telegram_connect_timeout)
        .read_timeout(config.telegram_connect_timeout)
        .get_updates_connect_timeout(config.telegram_connect_timeout)
        .get_updates_read_timeout(config.telegram_connect_timeout)
    )
    if config.telegram_proxy:
        builder = builder.proxy(config.telegram_proxy).get_updates_proxy(
            config.telegram_proxy
        )
        logger.info("Telegram API через прокси: %s", config.telegram_proxy)
    if config.telegram_api_base_url:
        builder = builder.base_url(config.telegram_api_base_url)
        logger.info("Telegram API base URL: %s", config.telegram_api_base_url)
    return builder


def build_application(config: BotConfig | None = None) -> Application:
    config = config or load_config()
    app = _application_builder(config).build()
    app.bot_data[CONFIG_KEY] = config

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler(SEARCH_COMMANDS, cmd_search))
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.Regex(r"(?i)^/(поиск|vкус)(?:@\w+)?(?:\s|$)"),
            handle_cyrillic_search_command,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(r"(?i)^(вкус|flavor|поиск|search)[\s:]"),
            handle_flavor_prefix,
        )
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_check_list))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_single_line))

    return app


def run_polling() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    config = load_config()
    if not config.telegram_proxy:
        logger.warning(
            "TELEGRAM_PROXY не задан. Если api.telegram.org недоступен, "
            "укажите прокси VPN (Clash/v2ray) в .env, например "
            "TELEGRAM_PROXY=http://127.0.0.1:7890"
        )
    app = build_application(config)
    logger.info("Бот запущен (polling)")
    app.run_polling(drop_pending_updates=True)
