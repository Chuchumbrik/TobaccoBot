"""Текстовое представление результатов сравнения (бот, CLI)."""

from __future__ import annotations

from .types import CompareListResult, CompareQuerySummary, CompareSearchResult


def _status_icon(status: str) -> str:
    if status == "есть":
        return "✅"
    if status == "нет":
        return "❌"
    if status == "не найден":
        return "❓"
    return "⚠️"


def format_compare_search(compare: CompareSearchResult) -> str:
    lines = [
        f"<b>Сравнение поиска:</b> «{compare.query}»",
        f"<i>Лимит {compare.limit} на сайт · сайтов: {len(compare.sites)}</i>",
        "",
    ]
    for site in compare.sites:
        if site.error:
            lines.append(f"<b>{site.site_name}</b> — ⚠️ {site.error}")
            continue
        r = site.result
        if r is None:
            lines.append(f"<b>{site.site_name}</b> — нет данных")
            continue
        in_stock = r.in_stock_count
        lines.append(
            f"<b>{site.site_name}</b> — найдено {len(r.hits)} "
            f"(в наличии: {in_stock})"
        )
        for i, hit in enumerate(r.hits[:5], 1):
            icon = _status_icon(hit.status)
            price = hit.product.price
            price_s = f" · {price:.0f} ₽" if price else ""
            lines.append(f"  {i}. {icon} {hit.product.name}{price_s}")
        if len(r.hits) > 5:
            lines.append(f"  … ещё {len(r.hits) - 5}")
        lines.append("")

    summary = summarize_prices_block(compare)
    if summary:
        lines.append(summary)
    return "\n".join(lines).strip()


def summarize_prices_block(compare: CompareSearchResult) -> str:
    from .compare import summarize_search_prices

    s = summarize_search_prices(compare)
    if not s.rows:
        return ""
    lines = ["<b>Сводка (лучшее на сайт):</b>"]
    for row in s.rows:
        if row.status == "ошибка":
            lines.append(f"• {row.site_name}: ошибка")
            continue
        if row.product_name is None:
            lines.append(f"• {row.site_name}: не найдено")
            continue
        price = f"{row.price:.0f} {row.currency or '₽'}" if row.price else "—"
        lines.append(
            f"• {row.site_name}: {_status_icon(row.status)} "
            f"{row.product_name} — {price}"
        )
    cheapest = s.cheapest_in_stock
    if cheapest:
        lines.append(
            f"\n💰 <b>Дешевле в наличии:</b> {cheapest.site_name} — "
            f"{cheapest.price:.0f} {cheapest.currency or '₽'}"
        )
    return "\n".join(lines)


def format_compare_list(compare: CompareListResult) -> str:
    if not compare.lines:
        return "Пустой список."
    header = " · ".join(
        compare.site_names.get(sid, sid) for sid in compare.site_ids
    )
    lines = [
        f"<b>Сравнение списка</b> ({len(compare.lines)} строк)",
        f"<i>{header}</i>",
        "",
    ]
    for row in compare.lines:
        lines.append(f"<b>{row.query}</b>")
        for sid in compare.site_ids:
            name = compare.site_names.get(sid, sid)
            if sid in row.errors:
                lines.append(f"  {name}: ⚠️ {row.errors[sid]}")
                continue
            check = row.by_site.get(sid)
            if check is None:
                lines.append(f"  {name}: —")
                continue
            icon = _status_icon(check.status)
            extra = ""
            if check.matched_name:
                extra = f" → {check.matched_name}"
            if check.price:
                extra += f" ({check.price:.0f} ₽)"
            lines.append(f"  {name}: {icon}{extra}")
        lines.append("")
    return "\n".join(lines).strip()


def format_query_summary(summary: CompareQuerySummary) -> str:
    lines = [f"<b>Запрос:</b> {summary.query}", ""]
    for row in summary.rows:
        if row.product_name is None:
            lines.append(f"• {row.site_name}: {row.status}")
            continue
        price = f"{row.price:.0f} {row.currency or '₽'}" if row.price else "—"
        lines.append(
            f"• {row.site_name}: {_status_icon(row.status)} "
            f"{row.product_name} — {price}"
        )
    cheapest = summary.cheapest_in_stock
    if cheapest:
        lines.append(
            f"\n💰 Дешевле в наличии: <b>{cheapest.site_name}</b> "
            f"({cheapest.price:.0f} {cheapest.currency or '₽'})"
        )
    return "\n".join(lines)
