"""Форматирование ответов бота."""

from __future__ import annotations

from oshisha.cart import CartAddBatchResult, CartAddResult
from oshisha.catalog import ProductCheckResult
from oshisha.flavor_search import FlavorSearchResult


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

    return header + "\n".join(blocks)


def format_cart_item(r: CartAddResult) -> str:
    if r.success:
        price = f", {r.line_price} ₽" if r.line_price else ""
        qty = f" ×{r.quantity}" if r.quantity > 1 else ""
        name = _esc(r.matched_name) if r.matched_name else _esc(r.query)
        return f"✅ <b>{_esc(r.query)}</b>{qty}{price}\n→ {name}"
    icon = "❓" if r.message == "не найден" else "❌"
    extra = f" → {_esc(r.matched_name)}" if r.matched_name else ""
    return f"{icon} <b>{_esc(r.query)}</b> — {r.message}{extra}"


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


def format_help() -> str:
    return (
        "<b>TBotTabak</b> — проверка табака на oshisha.cc\n\n"
        "Сначала выберите действие <b>кнопкой внизу</b> — бот подскажет, "
        "что отправить. Можно и командами:\n\n"
        "<b>🔍 Поиск по вкусу</b> — <code>/search</code>\n"
        "Пример: <code>малина 200</code>\n\n"
        "<b>📦 Проверка</b> — одна строка <code>/check</code> или список <code>/list</code>\n\n"
        "<b>🛒 В корзину</b> — <code>/cart</code> или список <code>/cartlist</code>\n"
        "Те же строки, что для проверки; на сайте нужен вход в аккаунт.\n\n"
        "<b>↩️ Отмена</b> — выйти из текущего шага."
    )


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
