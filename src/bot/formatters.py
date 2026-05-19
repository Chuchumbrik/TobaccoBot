"""Форматирование ответов бота."""

from __future__ import annotations

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


def format_help() -> str:
    return (
        "<b>TBotTabak</b> — проверка табака на oshisha.cc\n\n"
        "<b>Поиск по вкусу</b>\n"
        "• <code>/search малина 200</code>\n"
        "• <code>/search арбуз дыня</code>\n"
        "• <code>/search кокос | must have</code>\n"
        "• <code>/poisk</code> или <code>/поиск</code> — то же\n\n"
        "<b>Проверка позиции</b>\n"
        "Одна строка: <code>бб мармелад кола 200</code>\n"
        "Или список — несколько строк.\n\n"
        "<b>Примеры строк</b>\n"
        "• <code>бб - мармелад кола 200</code>\n"
        "• <code>сарма малина 200</code>\n"
        "• <code>арбуз-дыня 200</code>"
    )


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
