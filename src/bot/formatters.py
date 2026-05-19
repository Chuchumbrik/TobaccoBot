"""Форматирование ответов бота."""

from __future__ import annotations

from oshisha.cart import CartAddBatchResult, CartAddResult, CartView
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

from bot.cart_log import CartLogEntry, CartLogState, format_session_started, format_ts_display
from bot.messages import (
    HINT_AFTER_CART,
    HINT_AFTER_CHECK,
    HINT_AFTER_CHECK_LIST,
    HINT_AFTER_SEARCH,
)


def format_check_result(r: ProductCheckResult) -> str:
    if r.status == "не найден":
        return f"❓ {r.query} — не найден"

    icon = "✅" if r.status == "есть" else "❌"
    parts = [f"{icon} <b>{_esc(r.query)}</b> — {r.status}"]
    if r.requested_weight_g and r.matched_weight_g and r.requested_weight_g != r.matched_weight_g:
        parts.append(f" (на сайте {r.matched_weight_g}г)")
    if r.price is not None:
        parts.append(f", {int(r.price)} ₽")
    if r.max_quantity is not None:
        parts.append(f", остаток {int(r.max_quantity)}")
    if r.pack_count > 1:
        parts.append(f", ×{r.pack_count}")

    lines = [" ".join(parts)]
    if r.matched_name:
        lines.append(f"→ {_esc(r.matched_name)}")
    return "\n".join(lines)


def format_flavor_search(result: FlavorSearchResult) -> str:
    if not result.hits:
        hint = result.parsed.summary() or result.query
        return (
            f"По вкусу «{_esc(result.query)}» ничего не нашёл.\n"
            f"<i>Разбор: {_esc(hint)}</i>"
        )

    header = (
        f"🔍 Вкус: <b>{_esc(result.query)}</b>\n"
        f"Найдено: {len(result.hits)} (в наличии: {result.in_stock_count})\n"
    )
    if result.flavor_keys_matched:
        header += f"<i>Словарь: {', '.join(result.flavor_keys_matched[:5])}</i>\n"
    header += "\n"

    blocks: list[str] = []
    for i, hit in enumerate(result.hits, 1):
        icon = "✅" if hit.status == "есть" else "❌"
        brand = f" [{_esc(hit.brand_display)}]" if hit.brand_display else ""
        line = f"{i}. {icon}{brand} {_esc(hit.product.name)}"
        extras: list[str] = []
        if hit.weight_note:
            extras.append(hit.weight_note)
        if hit.product.price is not None:
            extras.append(f"{int(hit.product.price)} ₽")
        if hit.product.max_quantity is not None and hit.status == "есть":
            extras.append(f"ост. {int(hit.product.max_quantity)}")
        if extras:
            line += f" — {', '.join(extras)}"
        blocks.append(line)

    return header + "\n".join(blocks) + HINT_AFTER_SEARCH


def format_flavor_pick_confirm(
    hit: FlavorSearchHit,
    *,
    list_number: int,
    variants_in_stock: int,
) -> str:
    brand = f"<b>{_esc(hit.brand_display)}</b> · " if hit.brand_display else ""
    lines = [
        "<b>🛒 Какой табак кладём в корзину?</b>",
    ]
    if variants_in_stock > 1:
        lines.append(
            f"<i>Вариант {list_number} из {variants_in_stock} в наличии</i>\n"
        )
    lines.append(f"✅ {brand}{_esc(hit.product.name)}")
    extras: list[str] = []
    if hit.weight_note:
        extras.append(hit.weight_note)
    if hit.product.price is not None:
        extras.append(f"{int(hit.product.price)} ₽")
    if hit.product.max_quantity is not None:
        extras.append(f"остаток {int(hit.product.max_quantity)}")
    if extras:
        lines.append(f"<i>{', '.join(extras)}</i>")
    lines.append("\nПодтвердите кнопкой ниже или выберите другой вариант.")
    return "\n".join(lines)


def format_check_pick_confirm(
    check: ProductCheckResult,
    *,
    list_number: int,
    variants_in_stock: int,
) -> str:
    lines = [
        "<b>🛒 Какой табак кладём в корзину?</b>",
    ]
    if variants_in_stock > 1:
        lines.append(
            f"<i>Позиция {list_number} из {variants_in_stock} в наличии</i>\n"
        )
    lines.append(f"✅ <b>{_esc(check.query)}</b>")
    if check.matched_name:
        lines.append(f"→ {_esc(check.matched_name)}")
    extras: list[str] = []
    if check.price is not None:
        extras.append(f"{int(check.price)} ₽")
    if check.max_quantity is not None:
        extras.append(f"остаток {int(check.max_quantity)}")
    if check.pack_count > 1:
        extras.append(f"×{check.pack_count} уп.")
    if extras:
        lines.append(f"<i>{', '.join(extras)}</i>")
    lines.append("\nПодтвердите кнопкой ниже или выберите другой вариант.")
    return "\n".join(lines)


def format_cart_item(r: CartAddResult) -> str:
    if r.success:
        price = f", {r.line_price} ₽" if r.line_price else ""
        qty = f" ×{r.quantity}" if r.quantity > 1 else ""
        name = _esc(r.matched_name) if r.matched_name else _esc(r.query)
        return f"✅ <b>{_esc(r.query)}</b>{qty}{price}\n→ {name}"
    icon = "❓" if r.message == "не найден" else "❌"
    extra = f" → {_esc(r.matched_name)}" if r.matched_name else ""
    return f"{icon} <b>{_esc(r.query)}</b> — {r.message}{extra}"


def format_site_cart(cart: CartView) -> str:
    if cart.empty or not cart.items:
        return (
            "🛒 <b>Корзина на сайте пуста</b>\n\n"
            f'<a href="{_esc(cart.cart_url)}">Открыть корзину</a>'
        )
    lines = [f"🛒 <b>Корзина на сайте</b> ({len(cart.items)} поз.)"]
    if cart.total_sum is not None:
        lines[0] += f" — <b>{int(cart.total_sum)} ₽</b>"
    lines.append("")
    for i, item in enumerate(cart.items, 1):
        qty = int(item.quantity) if item.quantity == int(item.quantity) else item.quantity
        price_part = ""
        if item.sum_price is not None:
            price_part = f" — {int(item.sum_price)} ₽"
        elif item.price is not None:
            price_part = f" — {int(item.price)} ₽"
        lines.append(f"{i}. {_esc(item.name)} ×{qty}{price_part}")
    lines.append("")
    lines.append(f'<a href="{_esc(cart.cart_url)}">Открыть корзину</a>')
    return "\n".join(lines)


def format_cart_log(
    entries: list[CartLogEntry],
    *,
    title: str,
    show_user: bool,
    state: CartLogState | None = None,
) -> str:
    header_lines = [f"<b>{_esc(title)}</b>"]
    if state is not None:
        header_lines.append(
            f"<i>Заказ №{state.session_id} · начат {format_session_started(state)} (МСК)</i>"
        )
    header_lines.append("")

    if not entries:
        header_lines.append("В этом заказе пока нет добавлений из бота.")
        header_lines.append(
            "\n<i>Чтобы начать новый заказ — кнопка «🔄 Новый заказ».</i>"
        )
        return "\n".join(header_lines)

    lines = header_lines
    for entry in reversed(entries):
        icon = "✅" if entry.success else "❌"
        when = format_ts_display(entry.ts)
        who = f" · <i>{_esc(entry.display_user())}</i>" if show_user else ""
        name = f"\n→ {_esc(entry.product_name)}" if entry.product_name else ""
        qty = f" ×{entry.quantity}" if entry.quantity > 1 else ""
        price = f", {entry.line_price} ₽" if entry.line_price else ""
        lines.append(
            f"{icon} <b>{when}</b>{who}\n"
            f"<code>{_esc(entry.query)}</code>{qty}{price}{name}"
        )
    lines.append("\n<i>🔄 Новый заказ — сброс журнала для следующего заказа.</i>")
    return "\n\n".join(lines)


def format_cart_batch(batch: CartAddBatchResult) -> str:
    lines = [format_cart_item(r) for r in batch.items]
    if not lines:
        return "Нет позиций для добавления."
    header = f"🛒 Добавлено: {batch.added_count} из {len(batch.items)}"
    if batch.cart_sum_price is not None:
        header += f"\nКорзина на сайте: {batch.cart_sum_price} ₽"
        if batch.cart_quantity:
            header += f" ({batch.cart_quantity} шт.)"
    header += f'\n<a href="{_esc(batch.cart_url)}">Открыть корзину</a>\n\n'
    return header + "\n\n".join(lines) + HINT_AFTER_CART


def format_check_results(results: list[ProductCheckResult]) -> str:
    chunks = [format_check_result(r) for r in results]
    if not chunks:
        return "Нет позиций."
    text = "\n\n".join(chunks)
    hint = HINT_AFTER_CHECK_LIST if len(results) > 1 else HINT_AFTER_CHECK
    return text + hint


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
