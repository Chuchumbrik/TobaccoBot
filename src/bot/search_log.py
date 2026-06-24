"""Журнал поисковых запросов.

Сохраняет: что искал пользователь, какой intent был определён,
сколько результатов нашлось и есть ли в наличии.

Используется для:
  - анализа популярных запросов
  - нахождения пробелов в таксономии (нулевые результаты)
  - улучшения правил classify_by_rules() в update_taxonomy.py
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEARCH_LOG_PATH = ROOT / "data" / "search_log.jsonl"
MAX_LOG_LINES = 10_000   # ротация: удаляем старые при превышении


@dataclass
class SearchLogEntry:
    ts: str                          # ISO timestamp UTC
    user_id: int
    query: str                       # исходный текст от пользователя
    intent: str                      # после extract_flavor_intent / normalize_query
    search_type: str                 # "flavor" | "mix" | "check" | "advise"
    results_count: int               # всего найдено
    in_stock_count: int              # из них в наличии
    top_names: list[str]             # первые 3 названия результатов
    llm_backend: str | None = None
    llm_calls: int | None = None
    llm_total_ms: int | None = None
    catalog_calls: int | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_search(
    *,
    path: Path | None = None,
    user_id: int,
    query: str,
    intent: str,
    search_type: str,
    results_count: int,
    in_stock_count: int,
    top_names: list[str],
    llm_backend: str | None = None,
    llm_calls: int | None = None,
    llm_total_ms: int | None = None,
    catalog_calls: int | None = None,
) -> None:
    """Добавить запись о поиске в журнал."""
    entry = SearchLogEntry(
        ts=_utc_now(),
        user_id=int(user_id) if user_id else 0,
        query=query.strip(),
        intent=intent.strip(),
        search_type=search_type,
        results_count=results_count,
        in_stock_count=in_stock_count,
        top_names=top_names[:3],
        llm_backend=llm_backend,
        llm_calls=llm_calls,
        llm_total_ms=llm_total_ms,
        catalog_calls=catalog_calls,
    )
    log_path = path or DEFAULT_SEARCH_LOG_PATH
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        _rotate_if_needed(log_path)
    except Exception as exc:
        logger.warning("search_log write failed: %s", exc)


def read_search_log(
    path: Path | None = None,
    limit: int = 100,
) -> list[SearchLogEntry]:
    """Читает последние N записей из журнала (сначала новые)."""
    log_path = path or DEFAULT_SEARCH_LOG_PATH
    if not log_path.exists():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
        result = []
        for line in reversed(lines[-limit:]):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                result.append(SearchLogEntry(**data))
            except Exception:
                pass
        return result
    except Exception as exc:
        logger.warning("search_log read failed: %s", exc)
        return []


def get_zero_result_queries(
    path: Path | None = None,
    min_occurrences: int = 2,
) -> list[tuple[str, int]]:
    """Запросы с нулевым результатом, встречавшиеся ≥ N раз.

    Returns: [(intent, count), ...] sorted by count desc
    """
    log_path = path or DEFAULT_SEARCH_LOG_PATH
    if not log_path.exists():
        return []
    counts: dict[str, int] = {}
    try:
        for line in log_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                if data.get("results_count", 1) == 0:
                    intent = data.get("query", data.get("intent", "")).strip().lower()
                    if not intent:
                        continue
                    # Фильтруем мусор: JSON-строки и многострочные списки
                    if intent.startswith("{") or intent.startswith("["):
                        continue
                    if "\n" in intent or len(intent) > 120:
                        continue
                    counts[intent] = counts.get(intent, 0) + 1
            except Exception:
                pass
    except Exception as exc:
        logger.warning("search_log zero-results failed: %s", exc)
        return []

    return sorted(
        [(k, v) for k, v in counts.items() if v >= min_occurrences],
        key=lambda x: x[1],
        reverse=True,
    )


def _rotate_if_needed(path: Path) -> None:
    """Удаляем первые 10% строк если файл слишком большой."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > MAX_LOG_LINES:
            keep = int(MAX_LOG_LINES * 0.9)
            path.write_text(
                "\n".join(lines[-keep:]) + "\n",
                encoding="utf-8",
            )
            logger.info("search_log rotated: kept %d lines", keep)
    except Exception:
        pass
