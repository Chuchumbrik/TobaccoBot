"""Форматирование ответов бота."""

from __future__ import annotations

import re

from oshisha import catalog_cache
from oshisha.cart import CartAddBatchResult, CartAddResult, CartView
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchHit, FlavorSearchResult

from bot.cart_log import CartLogEntry, CartLogState, format_session_started, format_ts_display
from bot.weight_groups import FlavorGroup, group_hits

# Паттерн «с ароматом» — всё до него включительно убирается из названия
_AROMAT_RE = re.compile(r'^.*?\bс\s+ароматом\s+', re.IGNORECASE | re.DOTALL)


def _compact_name(base_name: str, brand_display: str | None = None) -> str:
    """Оставляет только вкусовую часть названия.

    «Chabacco Medium с ароматом Дикая клубника» → «Дикая клубника»
    «Duft Strawberry» → «Strawberry»
    """
    name = base_name
    m = _AROMAT_RE.match(name)
    if m:
        return name[m.end():].strip()
    if brand_display:
        # Убрать «Бренд [Medium/Light/HiT/Mix…]» из начала
        after = re.sub(
            r'^' + re.escape(brand_display) + r'(?:\s+(?:Medium|Light|Hard|Strong|Classic|HiT|Mix|Salt))?\s*',
            '', name, flags=re.IGNORECASE,
        ).strip()
        if after and len(after) < len(name):
            return after
    return name


def format_check_result(r: ProductCheckResult) -> str:
    if r.status == "не найден":
        return f"❓ {r.query} — не найден"

    weight_mismatch = bool(
        r.requested_weight_g
        and r.matched_weight_g
        and r.requested_weight_g != r.matched_weight_g
    )
    if r.status == "есть":
        icon = "⚠️" if weight_mismatch else "✅"
    else:
        icon = "❌"
    status_text = "только другая фасовка" if weight_mismatch and r.status == "есть" else r.status
    parts = [f"{icon} <b>{_esc(r.query)}</b> — {status_text}"]
    if weight_mismatch:
        parts.append(f" (запрошено {r.requested_weight_g}г, на сайте {r.matched_weight_g}г)")
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


def format_hit_groups_lines(
    hits: list[FlavorSearchHit],
    *,
    page: int = 0,
    page_size: int | None = None,
) -> list[str]:
    """Форматирует список хитов с группировкой по граммовкам.

    Двухстрочный компактный формат:
      N. ✅ Бренд · Вкус
         100г / 200г · от 450₽

    page / page_size — поддержка пагинации (page_size=None → все элементы).
    Используется и в поиске по вкусу, и в советнике.
    """
    groups = group_hits(hits)
    if page_size is not None:
        start = page * page_size
        page_groups = groups[start:start + page_size]
        offset = start
    else:
        page_groups = groups
        offset = 0

    lines: list[str] = []
    for i, group in enumerate(page_groups, offset + 1):
        icon = "✅" if group.status == "есть" else "❌"
        brand_part = f"<b>{_esc(group.brand_display)}</b> · " if group.brand_display else ""
        flavor = _esc(_compact_name(group.base_name, group.brand_display))

        # Строка 1: номер, статус, бренд (жирный), вкус
        line = f"{i}. {icon} {brand_part}{flavor}"

        # Строка 2: граммовки + цена (с отступом)
        if group.is_grouped:
            w_parts: list[str] = []
            for v in group.variants:
                w_str = f"{v.weight_g}г" if v.weight_g else "?"
                w_parts.append(w_str if v.hit.status == "есть" else f"<s>{w_str}</s>")
            weights_str = " / ".join(w_parts)
            prices = [
                v.hit.product.price for v in group.in_stock_variants
                if v.hit.product.price is not None
            ]
            price_str = ""
            if prices:
                if len(set(prices)) == 1:
                    price_str = f" · {int(prices[0])}₽"
                else:
                    price_str = f" · от {int(min(prices))}₽"
            line += f"\n   {weights_str}{price_str}"
        else:
            hit = group.variants[0].hit
            details: list[str] = []
            w = group.variants[0].weight_g
            if hit.weight_note:
                details.append(hit.weight_note)
            elif w:
                details.append(f"{w}г")
            if hit.product.price is not None:
                details.append(f"{int(hit.product.price)}₽")
            if hit.product.max_quantity is not None and hit.status == "есть":
                details.append(f"ост.{int(hit.product.max_quantity)}")
            if details:
                line += f"\n   {' · '.join(details)}"

        lines.append(line)
    return lines


def format_flavor_search(result: FlavorSearchResult) -> str:
    if not result.hits:
        hint = result.parsed.summary() or result.query
        return (
            f"По вкусу «{_esc(result.query)}» ничего не нашёл.\n"
            f"<i>Разбор: {_esc(hint)}</i>"
        )

    groups = group_hits(result.hits)
    in_stock_groups = sum(1 for g in groups if g.status == "есть")
    header = (
        f"🔍 Вкус: <b>{_esc(result.query)}</b>\n"
        f"Найдено: {len(groups)} (в наличии: {in_stock_groups})\n"
    )
    if result.flavor_keys_matched:
        header += f"<i>Словарь: {', '.join(result.flavor_keys_matched[:5])}</i>\n"
    header += catalog_cache.stock_disclaimer_html()
    header += "\n"

    return header + "\n".join(format_hit_groups_lines(result.hits))


def format_flavor_weight_group(group: FlavorGroup) -> str:
    """Текст для сообщения выбора граммовки (weight-picker)."""
    brand = f"<b>{_esc(group.brand_display)}</b> · " if group.brand_display else ""
    in_stock_w = [
        f"{v.weight_g} гр" for v in group.in_stock_variants if v.weight_g
    ]
    weights_str = " / ".join(in_stock_w) if in_stock_w else ""
    header = f"<b>🛒 Выберите граммовку:</b>\n{brand}{_esc(group.base_name)}"
    if weights_str:
        header += f"\n<i>Доступно: {weights_str}</i>"
    return header


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
    return header + "\n\n".join(lines)


def format_check_results(results: list[ProductCheckResult]) -> str:
    chunks = [format_check_result(r) for r in results]
    if not chunks:
        return "Нет позиций."
    footer = catalog_cache.stock_disclaimer_html()
    body = "\n\n".join(chunks)
    return f"{body}\n\n{footer}" if footer else body


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
