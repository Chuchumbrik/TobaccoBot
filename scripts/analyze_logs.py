#!/usr/bin/env python3
"""Анализ дневных логов запросов и ответов бота TBotTabak.

Использование:
    python scripts/analyze_logs.py               # сегодня
    python scripts/analyze_logs.py --date 2026-05-25
    python scripts/analyze_logs.py --days 7      # последние 7 дней
    python scripts/analyze_logs.py --intent flavor_search  # только поиск
    python scripts/analyze_logs.py --top 20      # топ-20 вместо топ-10
    python scripts/analyze_logs.py --pairs       # показать Q/A пары
    python scripts/analyze_logs.py --pairs --intent advise  # пары только советника

Формат логов: data/user_logs/YYYY-MM-DD.jsonl
  Q-записи — запросы пользователей
  A-записи — ответы бота (preview первых 400 символов без HTML)
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "data" / "user_logs"

# Цвета для терминала
_COLOR = sys.stdout.isatty()
BOLD   = "\033[1m"    if _COLOR else ""
DIM    = "\033[2m"    if _COLOR else ""
CYAN   = "\033[36m"   if _COLOR else ""
GREEN  = "\033[32m"   if _COLOR else ""
YELLOW = "\033[33m"   if _COLOR else ""
RED    = "\033[31m"   if _COLOR else ""
RESET  = "\033[0m"    if _COLOR else ""

INTENT_ORDER = [
    "flavor_search", "advise", "mix", "refine",
    "theme", "chat", "check", "cart_add",
    "ack", "advise_clarify", "command",
]

INTENT_LABEL = {
    "flavor_search":  "🔍 Поиск по вкусу",
    "advise":         "🎯 Советник",
    "mix":            "🎨 Миксы",
    "refine":         "✏️  Уточнение",
    "theme":          "🌿 Тема",
    "chat":           "💬 Чат",
    "check":          "📦 Проверка позиций",
    "cart_add":       "🛒 Добавление в корзину",
    "ack":            "👍 Подтверждение (ок/спс)",
    "advise_clarify": "❓ Уточняющий ответ",
    "command":        "⌨️  Команды",
}


def load_day(day: str) -> list[dict]:
    path = LOG_DIR / f"{day}.jsonl"
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return entries


def _bar(count: int, total: int, width: int = 20) -> str:
    filled = round(count / total * width) if total else 0
    return "█" * filled + "░" * (width - filled)


def _only_queries(entries: list[dict], intent_filter: str | None = None) -> list[dict]:
    """Только Q-записи (запросы), с опциональным фильтром по intent."""
    return [
        e for e in entries
        if e.get("type") == "Q"
        and (intent_filter is None or e.get("intent") == intent_filter)
    ]


def _build_pairs(entries: list[dict], intent_filter: str | None = None) -> list[tuple[dict, dict | None]]:
    """Собирает пары (Q, A) по user_id и хронологии."""
    pairs: list[tuple[dict, dict | None]] = []
    i = 0
    while i < len(entries):
        e = entries[i]
        if e.get("type") == "Q":
            if intent_filter and e.get("intent") != intent_filter:
                i += 1
                continue
            # Ищем следующую A для того же user_id
            answer = None
            for j in range(i + 1, min(i + 5, len(entries))):
                cand = entries[j]
                if cand.get("type") == "A" and cand.get("user_id") == e.get("user_id"):
                    answer = cand
                    break
            pairs.append((e, answer))
        i += 1
    return pairs


def analyze_day(entries: list[dict], day: str, top_n: int = 10) -> None:
    queries = _only_queries(entries)
    if not queries:
        print(f"\n{DIM}─── {day} — нет данных{RESET}")
        return

    users = {e["user_id"] for e in queries if e.get("user_id")}
    intents = Counter(e.get("intent", "?") for e in queries)
    total = len(queries)

    print(f"\n{BOLD}{CYAN}─── {day} ───────────────────────────────────────{RESET}")
    print(f"{BOLD}Запросов: {total}   Уникальных пользователей: {len(users)}{RESET}")

    # ── По intent ──────────────────────────────────────────────────────────────
    print(f"\n{BOLD}По типу запроса:{RESET}")
    shown = set()
    for key in INTENT_ORDER:
        if key in intents:
            cnt = intents[key]
            label = INTENT_LABEL.get(key, key)
            bar = _bar(cnt, total)
            pct = cnt / total * 100
            print(f"  {label:<30} {YELLOW}{cnt:>4}{RESET}  {DIM}{bar}{RESET}  {pct:.0f}%")
            shown.add(key)
    for key, cnt in intents.most_common():
        if key not in shown:
            bar = _bar(cnt, total)
            pct = cnt / total * 100
            print(f"  {key:<30} {YELLOW}{cnt:>4}{RESET}  {DIM}{bar}{RESET}  {pct:.0f}%")

    # ── Топ-N поисков по вкусу ─────────────────────────────────────────────────
    flavor = [e["text"] for e in queries if e.get("intent") == "flavor_search"]
    if flavor:
        top = Counter(t.lower().strip() for t in flavor)
        print(f"\n{BOLD}Топ-{min(top_n, len(top))} поисков по вкусу{RESET} ({len(flavor)} всего):")
        for i, (text, cnt) in enumerate(top.most_common(top_n), 1):
            marker = f" ×{cnt}" if cnt > 1 else ""
            print(f"  {DIM}{i:>2}.{RESET} {text}{GREEN}{marker}{RESET}")

    # ── Советник и миксы ────────────────────────────────────────────────────────
    advise = [e for e in queries if e.get("intent") in ("advise", "mix")]
    if advise:
        print(f"\n{BOLD}Советник и миксы{RESET} ({len(advise)} всего):")
        for i, e in enumerate(advise[:top_n], 1):
            tag = "🎨 микс" if e.get("intent") == "mix" else "🎯 советник"
            print(f"  {DIM}{i:>2}.{RESET} [{tag}] {e['text'][:80]}")

    # ── Тематические запросы ────────────────────────────────────────────────────
    themes = [e["text"] for e in queries if e.get("intent") == "theme"]
    if themes:
        print(f"\n{BOLD}Тематические запросы{RESET} ({len(themes)}):")
        for i, t in enumerate(themes[:top_n], 1):
            print(f"  {DIM}{i:>2}.{RESET} {t[:80]}")

    # ── Активность по часам ─────────────────────────────────────────────────────
    hours = Counter(
        e["ts"][11:13]
        for e in queries
        if e.get("ts") and len(e["ts"]) >= 13
    )
    if hours:
        print(f"\n{BOLD}Активность по часам:{RESET}")
        for h in sorted(hours):
            cnt = hours[h]
            bar = "█" * min(cnt, 30)
            print(f"  {h}:00  {DIM}{bar}{RESET}  {cnt}")


def show_pairs(entries: list[dict], day: str, intent_filter: str | None, top_n: int) -> None:
    """Показать Q/A пары — запрос пользователя + ответ бота."""
    pairs = _build_pairs(entries, intent_filter)
    if not pairs:
        print(f"\n{DIM}─── {day} — нет данных{RESET}")
        return

    shown = pairs[:top_n]
    filter_str = f" [{intent_filter}]" if intent_filter else ""
    print(f"\n{BOLD}{CYAN}─── {day}{filter_str} — {len(pairs)} пар Q/A (показываю {len(shown)}){RESET}")

    for q, a in shown:
        ts = q.get("ts", "")
        user = q.get("username") or str(q.get("user_id", "?"))
        intent = q.get("intent", "?")
        intent_label = INTENT_LABEL.get(intent, intent)

        print(f"\n{DIM}{ts[11:19]}  @{user}  {intent_label}{RESET}")
        print(f"  {YELLOW}Q:{RESET} {q.get('text', '')[:120]}")

        if a:
            preview = a.get("preview", "")
            # Первую строку выделяем, остальные — с отступом
            lines = preview.split("\n")
            print(f"  {GREEN}A:{RESET} {lines[0][:120]}")
            for line in lines[1:5]:
                if line.strip():
                    print(f"     {DIM}{line[:120]}{RESET}")
        else:
            print(f"  {RED}A: (нет ответа в логе){RESET}")


def analyze_range(
    days_list: list[str],
    top_n: int,
    intent_filter: str | None,
    show_pairs_mode: bool,
) -> None:
    if len(days_list) == 1:
        entries = load_day(days_list[0])
        if show_pairs_mode:
            show_pairs(entries, days_list[0], intent_filter, top_n)
        else:
            queries = _only_queries(entries, intent_filter)
            analyze_day(queries + [e for e in entries if e.get("type") != "Q"], days_list[0], top_n)
            # Переделаем — analyze_day работает с queries
            _analyze_day_entries(entries, days_list[0], top_n, intent_filter)
        return

    # Несколько дней — сводная статистика
    all_entries: list[dict] = []
    for day in days_list:
        all_entries.extend(load_day(day))

    all_queries = _only_queries(all_entries, intent_filter)
    if not all_queries:
        print("Нет данных за указанный период.")
        return

    if show_pairs_mode:
        for day in days_list:
            entries = load_day(day)
            show_pairs(entries, day, intent_filter, top_n)
        return

    users_total = {e["user_id"] for e in all_queries if e.get("user_id")}
    intents = Counter(e.get("intent", "?") for e in all_queries)
    daily = Counter(e["ts"][:10] for e in all_queries if e.get("ts"))

    print(f"\n{BOLD}{CYAN}═══ {days_list[-1]} .. {days_list[0]} ({len(days_list)} дней) ═══{RESET}")
    print(f"{BOLD}Запросов всего: {len(all_queries)}   Уникальных пользователей: {len(users_total)}{RESET}")
    print(f"{BOLD}В среднем за день: {len(all_queries)/len(days_list):.0f}{RESET}")

    print(f"\n{BOLD}По типу запроса:{RESET}")
    total = len(all_queries)
    shown = set()
    for key in INTENT_ORDER:
        if key in intents:
            cnt = intents[key]
            label = INTENT_LABEL.get(key, key)
            bar = _bar(cnt, total)
            pct = cnt / total * 100
            print(f"  {label:<30} {YELLOW}{cnt:>5}{RESET}  {DIM}{bar}{RESET}  {pct:.0f}%")
            shown.add(key)

    flavor = [e["text"] for e in all_queries if e.get("intent") == "flavor_search"]
    if flavor:
        top = Counter(t.lower().strip() for t in flavor)
        print(f"\n{BOLD}Топ-{min(top_n, len(top))} поисков по вкусу{RESET} ({len(flavor)} всего):")
        for i, (text, cnt) in enumerate(top.most_common(top_n), 1):
            print(f"  {DIM}{i:>2}.{RESET} {text}{GREEN} ×{cnt}{RESET}")

    print(f"\n{BOLD}По дням:{RESET}")
    for day in sorted(daily):
        cnt = daily[day]
        bar = "█" * min(cnt // 2, 40)
        print(f"  {day}  {DIM}{bar}{RESET}  {cnt}")


def _analyze_day_entries(entries: list[dict], day: str, top_n: int, intent_filter: str | None) -> None:
    """Анализ одного дня с учётом фильтра intent."""
    queries = _only_queries(entries, intent_filter)
    if not queries:
        print(f"\n{DIM}─── {day} — нет данных{RESET}")
        return

    users = {e["user_id"] for e in queries if e.get("user_id")}
    intents = Counter(e.get("intent", "?") for e in queries)
    total = len(queries)

    print(f"\n{BOLD}{CYAN}─── {day} ───────────────────────────────────────{RESET}")
    filter_note = f"  {DIM}[фильтр: {intent_filter}]{RESET}" if intent_filter else ""
    print(f"{BOLD}Запросов: {total}{RESET}{filter_note}   Уникальных пользователей: {len(users)}")

    # ── По intent ──────────────────────────────────────────────────────────────
    if not intent_filter:
        print(f"\n{BOLD}По типу запроса:{RESET}")
        shown = set()
        for key in INTENT_ORDER:
            if key in intents:
                cnt = intents[key]
                label = INTENT_LABEL.get(key, key)
                bar = _bar(cnt, total)
                pct = cnt / total * 100
                print(f"  {label:<30} {YELLOW}{cnt:>4}{RESET}  {DIM}{bar}{RESET}  {pct:.0f}%")
                shown.add(key)
        for key, cnt in intents.most_common():
            if key not in shown:
                bar = _bar(cnt, total)
                pct = cnt / total * 100
                print(f"  {key:<30} {YELLOW}{cnt:>4}{RESET}  {DIM}{bar}{RESET}  {pct:.0f}%")

    # ── Топ-N поисков по вкусу ─────────────────────────────────────────────────
    flavor = [e["text"] for e in queries if e.get("intent") == "flavor_search"]
    if flavor:
        top = Counter(t.lower().strip() for t in flavor)
        print(f"\n{BOLD}Топ-{min(top_n, len(top))} поисков по вкусу{RESET} ({len(flavor)} всего):")
        for i, (text, cnt) in enumerate(top.most_common(top_n), 1):
            marker = f" ×{cnt}" if cnt > 1 else ""
            print(f"  {DIM}{i:>2}.{RESET} {text}{GREEN}{marker}{RESET}")

    # ── Советник и миксы ────────────────────────────────────────────────────────
    advise = [e for e in queries if e.get("intent") in ("advise", "mix")]
    if advise:
        print(f"\n{BOLD}Советник и миксы{RESET} ({len(advise)} всего):")
        for i, e in enumerate(advise[:top_n], 1):
            tag = "🎨 микс" if e.get("intent") == "mix" else "🎯 советник"
            print(f"  {DIM}{i:>2}.{RESET} [{tag}] {e['text'][:80]}")

    # ── Тематические запросы ────────────────────────────────────────────────────
    themes = [e["text"] for e in queries if e.get("intent") == "theme"]
    if themes:
        print(f"\n{BOLD}Тематические запросы{RESET} ({len(themes)}):")
        for i, t in enumerate(themes[:top_n], 1):
            print(f"  {DIM}{i:>2}.{RESET} {t[:80]}")

    # ── Активность по часам ─────────────────────────────────────────────────────
    hours = Counter(
        e["ts"][11:13]
        for e in queries
        if e.get("ts") and len(e["ts"]) >= 13
    )
    if hours:
        print(f"\n{BOLD}Активность по часам:{RESET}")
        for h in sorted(hours):
            cnt = hours[h]
            bar = "█" * min(cnt, 30)
            print(f"  {h}:00  {DIM}{bar}{RESET}  {cnt}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Анализ дневных логов TBotTabak",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--date", "-d", help="Дата YYYY-MM-DD (по умолчанию: сегодня)")
    parser.add_argument("--days", "-n", type=int, default=1,
                        help="Количество последних дней (по умолчанию: 1)")
    parser.add_argument("--intent", "-i",
                        help="Фильтр по intent (flavor_search, advise, mix, theme, …)")
    parser.add_argument("--top", "-t", type=int, default=10,
                        help="Сколько топ-запросов показывать (по умолчанию: 10)")
    parser.add_argument("--pairs", "-p", action="store_true",
                        help="Показать Q/A пары (запрос → ответ бота)")
    parser.add_argument("--list-days", action="store_true",
                        help="Показать доступные файлы логов")
    args = parser.parse_args()

    if args.list_days:
        files = sorted(LOG_DIR.glob("*.jsonl"), reverse=True)
        if not files:
            print(f"Нет логов в {LOG_DIR}")
        else:
            print(f"Доступные логи ({len(files)}):")
            for f in files:
                lines = sum(1 for _ in f.open(encoding="utf-8"))
                size_kb = f.stat().st_size // 1024
                print(f"  {f.name}  {lines} строк  {size_kb} KB")
        return

    if args.date:
        days_list = [args.date]
    else:
        today = date.today()
        days_list = [
            (today - timedelta(days=i)).strftime("%Y-%m-%d")
            for i in range(args.days)
        ]

    if len(days_list) == 1:
        entries = load_day(days_list[0])
        if args.pairs:
            show_pairs(entries, days_list[0], args.intent, args.top)
        else:
            _analyze_day_entries(entries, days_list[0], args.top, args.intent)
    else:
        analyze_range(days_list, args.top, args.intent, args.pairs)


if __name__ == "__main__":
    main()
