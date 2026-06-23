"""Тексты бота: приветствие, подсказки, справка."""

from __future__ import annotations

from bot.config import BotConfig
from bot.menu_state import (
    MODE_CART_LIST,
    MODE_CART_SINGLE,
    MODE_COMPARE,
    MODE_FLAVOR,
    MODE_LIST,
    MODE_SINGLE,
)

MODE_LABELS = {
    MODE_FLAVOR: "🔍 жду запрос для поиска по вкусу",
    MODE_SINGLE: "📦 жду строку для проверки",
    MODE_LIST: "📝 жду список для проверки",
    MODE_CART_SINGLE: "🛒 жду строку для корзины",
    MODE_CART_LIST: "🛒 жду список для корзины",
    MODE_COMPARE: "⚖️ жду запрос для сравнения",
}


def format_welcome(
    *,
    active_mode: str | None = None,
    compare_available: bool = False,
    compare_sites: list[tuple[str, str]] | None = None,
) -> str:
    lines = [
        "<b>TBotTabak</b> — табак и наличие на <b>oshisha.cc</b>",
        "",
        "<b>Частые сценарии</b>",
        "• Не знаете что хотите → /advise — опишите словами, ИИ подберёт",
        "• Знаете вкус → /search или просто напишите название",
        "• Есть строка из прайса → /check или /list (несколько строк)",
        "• Собрать заказ → кнопки 🛒 под результатом → /cartview",
        "• Кто что добавил → /cartlog · новый заказ → /logreset",
    ]
    if compare_available and compare_sites:
        names = ", ".join(name for _, name in compare_sites)
        lines.append(
            f"• Сравнить цены и наличие → /compare ({names})"
        )
    lines.extend(
        [
            "",
            "Все команды и подробности: /help",
        ]
    )
    if active_mode and active_mode in MODE_LABELS:
        lines.insert(2, f"<i>Сейчас: {MODE_LABELS[active_mode]}</i>\n")
    return "\n".join(lines)


def format_help_full(
    config: BotConfig,
    *,
    user_id: int = 0,
    is_admin_log: bool = False,
    compare_available: bool = False,
    compare_sites: list[tuple[str, str]] | None = None,
) -> str:
    max_lines = config.check_list_max_lines
    journal_note = (
        "Видны <b>все</b> добавления из бота (вы администратор)."
        if is_admin_log
        else "Видны только <b>ваши</b> добавления."
        if config.telegram_admin_ids
        else "Видны <b>все</b> добавления команды (админы не заданы в .env)."
    )

    compare_block = ""
    if compare_available and compare_sites:
        names = ", ".join(name for _, name in compare_sites)
        compare_block = f"""
━━━━━━━━━━━━━━━━━━━━
<b>⚖️ Сравнение магазинов</b>
<i>Когда:</i> один запрос — результаты на всех подключённых сайтах ({names}).
<b>Кнопка:</b> ⚖️ Сравнить · <b>Команда:</b> /compare · /sravn

<b>Как:</b> одна строка — как поиск по вкусу; несколько строк — как проверка списка.
<b>Пример:</b> <code>/compare малина 200</code>
"""

    compare_cmd = " · /compare — сравнение сайтов" if compare_available else ""

    return f"""<b>📖 Справка TBotTabak</b>

<b>Общий принцип</b>
Сначала кнопка или команда → бот объясняет формат → вы отправляете текст → ответ.
Прервать шаг: кнопка <b>❌ Отмена</b> под сообщением, в меню или <b>🏠 Меню</b>. Справка: /help

━━━━━━━━━━━━━━━━━━━━
<b>🎯 Советник (ИИ-подбор)</b>
<i>Когда:</i> не знаете точное название — опишите что хотите словами.
<b>Кнопка:</b> 🎯 Советник
<b>Команды:</b> /advise · /sovet · /podber

<b>Как:</b> нажмите кнопку → опишите свободным текстом.
<b>Примеры:</b>
• <code>хочу сладенькое и ягодное</code>
• <code>что-то свежее, но без мяты</code>
• <code>кисло-сладкое, лёгкое</code>

Или просто напишите такой запрос без команды — бот определит сам.

<i>После подборки:</i> <b>🛒 Выбрать</b> или <b>✏️ Уточнить запрос</b> — добавить условия.

━━━━━━━━━━━━━━━━━━━━
<b>🎨 Тематический поиск</b>
<i>Когда:</i> хотите просмотреть целую вкусовую категорию.
<b>Команда:</b> /theme

<b>Примеры:</b>
• <code>/theme выпечка</code> — ваниль, корица, яблочный пирог…
• <code>/theme травянистые</code> — мята, базилик, тархун…
• <code>/theme пряные</code> — корица, имбирь, кардамон…
• <code>/theme цветочные</code> — жасмин, лаванда, роза…

Или просто напишите одно слово-тему без команды.

━━━━━━━━━━━━━━━━━━━━
<b>🔍 Поиск по вкусу</b>
<i>Когда:</i> нужно увидеть все варианты по вкусу/граммовке на сайте.
<b>Кнопка:</b> 🔍 Поиск по вкусу
<b>Команды:</b> /search · /poisk · /поиск

<b>Как:</b> нажмите кнопку → отправьте запрос одной строкой.
<b>Примеры:</b>
• <code>малина 200</code>
• <code>арбуз дыня</code>
• <code>кокос | must have</code> — только бренд Must Have

Сразу без кнопки: <code>/search малина 200</code>

<i>После поиска:</i> <b>Выбрать N</b> → уточнение варианта → <b>Положить в корзину</b>.
{compare_block}
━━━━━━━━━━━━━━━━━━━━
<b>📦 Проверить одну позицию</b>
<i>Когда:</i> есть точная строка из прайса — нужны наличие и цена.
<b>Кнопка:</b> 📦 Проверить
<b>Команда:</b> /check

<b>Как:</b> кнопка → одна строка в сообщении.
<b>Примеры:</b>
• <code>66 мармелад кола 200</code>
• <code>бб черешня 200</code>
• <code>сарма малина 200 3х</code> — запрос ×3 упаковок

Сразу: <code>/check 66 мармелад кола 200</code>

━━━━━━━━━━━━━━━━━━━━
<b>📝 Проверить список</b>
<i>Когда:</i> много позиций сразу (прайс, заказ клиента).
<b>Кнопка:</b> 📝 Список
<b>Команда:</b> /list

<b>Как:</b> кнопка → одно сообщение, <b>каждая позиция с новой строки</b> (минимум 2, максимум {max_lines}).
<b>Пример:</b>
<code>66 мармелад кола 200
сарма малина 200
арбуз-дыня 200</code>

━━━━━━━━━━━━━━━━━━━━
<b>🛒 Добавить в корзину</b>
<i>Когда:</i> позиция уже найдена — положить в общую корзину Oshisha.
<b>Как:</b> после поиска или проверки — <b>Выбрать N</b>, затем подтвердить вариант.
<b>Команды (без кнопок меню):</b> <code>/cart строка</code> · /cartlist — список строк

━━━━━━━━━━━━━━━━━━━━
<b>👀 Корзина на сайте</b>
<i>Когда:</i> посмотреть, что уже лежит в корзине, и сумму.
<b>Кнопка:</b> 👀 Корзина
<b>Команда:</b> /cartview

━━━━━━━━━━━━━━━━━━━━
<b>📜 Журнал добавлений</b>
<i>Когда:</i> кто, <b>когда</b> и что добавлял в текущем заказе.
<b>Кнопка:</b> 📜 Журнал · <b>Команда:</b> /cartlog

Время — по Москве. Журнал ведётся по «заказам».

<b>🔄 Новый заказ</b> — закрыть текущий журнал и начать следующий
(кнопка или /logreset). Старые записи сохраняются, в журнале виден только активный заказ.

{journal_note}

━━━━━━━━━━━━━━━━━━━━
<b>✏️ Формат строки (проверка и корзина)</b>
Пишите как в прайсе: бренд, вкус, граммовка.
• <code>бб</code>, <code>66</code>, <code>сарма</code> — сокращения брендов
• <code>200</code> или <code>200г</code> — вес
• <code>3х</code> или <code>x3</code> в конце — количество упаковок

━━━━━━━━━━━━━━━━━━━━
<b>⌨️ Все команды</b>
/start — меню · /help — эта справка
/advise — 🎯 советник (ИИ-подбор) · /theme — 🎨 тематический · /search — поиск по вкусу
/check · /list — проверка · /cart · /cartlist — в корзину
/cartview · /cartlog · /logreset · /menu{compare_cmd}"""


def format_help_chunks(
    config: BotConfig,
    *,
    user_id: int = 0,
    is_admin_log: bool = False,
    compare_available: bool = False,
    compare_sites: list[tuple[str, str]] | None = None,
) -> list[str]:
    """Разбить справку на части, если не влезает в лимит Telegram."""
    full = format_help_full(
        config,
        user_id=user_id,
        is_admin_log=is_admin_log,
        compare_available=compare_available,
        compare_sites=compare_sites,
    )
    if len(full) <= 4000:
        return [full]
    # запасной разрез по разделам
    parts = full.split("━━━━━━━━━━━━━━━━━━━━")
    chunks: list[str] = []
    buf = parts[0]
    for part in parts[1:]:
        piece = "━━━━━━━━━━━━━━━━━━━━" + part
        if len(buf) + len(piece) > 3900:
            chunks.append(buf.strip())
            buf = piece
        else:
            buf += piece
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [full[:4000]]


