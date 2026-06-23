"""
Таксономия вкусов: маппинг профилей → реальные поисковые запросы по каталогу.

Проблема, которую решает этот модуль:
  LLM генерирует компонент микса «кислый» или «ягодный» —
  такого слова в каталоге нет, поиск ничего не найдёт.

  resolve_component("кислый") → ["грейпфрут", "лимон", "кислого ананаса"]
  → ищем по каждому термину → находим реальные позиции ✅

  resolve_component("малина") → ["малина"]  ← уже конкретное название, ищем напрямую
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_TAXONOMY_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "vocab" / "flavor_taxonomy.json"
)
_PENDING_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "vocab" / "taxonomy_pending.jsonl"
)


@lru_cache(maxsize=1)
def _load_profiles() -> dict[str, list[str]]:
    """Загружает профили из файла. Результат кешируется до сброса."""
    try:
        if _TAXONOMY_FILE.exists():
            data = json.loads(_TAXONOMY_FILE.read_text(encoding="utf-8"))
            profiles = {k.lower().strip(): v for k, v in data.get("profiles", {}).items()}
            logger.info("Taxonomy loaded: %d profiles from %s", len(profiles), _TAXONOMY_FILE)
            return profiles
    except Exception as exc:
        logger.warning("Taxonomy load failed: %s", exc)
    return {}


def resolve_component(component: str, max_terms: int = 3) -> list[str]:
    """
    Конвертирует компонент микса в список поисковых запросов.

    Логика:
      1. Если слово — профильное (кислый, ягодный...) → берём реальные вкусы из таксономии.
      2. Иначе — ищем напрямую (уже конкретное название, напр. «малина»).

    Args:
        component:  слово от LLM, напр. «кислый» или «малина кислая»
        max_terms:  максимум поисковых запросов (по умолчанию 3)

    Returns:
        Список строк для поиска в каталоге.
    """
    profiles = _load_profiles()
    key = component.lower().strip()

    if key in profiles:
        terms = profiles[key][:max_terms]
        logger.debug("Taxonomy hit: %r → %s", component, terms)
        return terms

    # Не нашли в таксономии — ищем напрямую, логируем для ручного/скриптового дополнения
    logger.debug("Taxonomy miss: %r → direct search", component)
    enqueue_taxonomy_pending(component)
    return [component]


def enqueue_taxonomy_pending(component: str) -> None:
    """Запись неизвестного профиля для последующего update_taxonomy."""
    key = component.lower().strip()
    if not key or is_profile_word(component):
        return
    try:
        _PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "component": key,
            },
            ensure_ascii=False,
        )
        with _PENDING_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        logger.warning("taxonomy_pending write failed: %s", exc)


def is_profile_word(component: str) -> bool:
    """Возвращает True если слово является профильным (есть в таксономии)."""
    return component.lower().strip() in _load_profiles()


def get_all_profiles() -> list[str]:
    """Список всех известных профилей (для отладки и обновления)."""
    return sorted(_load_profiles().keys())


def save_new_profile(profile: str, search_terms: list[str]) -> bool:
    """
    Добавляет новый профиль в таксономию и сохраняет в файл.
    Возвращает True если профиль был новым (был добавлен).

    Используется для автоматического расширения таксономии через LLM.
    """
    key = profile.lower().strip()
    if not key or not search_terms:
        return False

    try:
        # Загружаем текущий файл (не кеш — читаем с диска)
        data: dict = {"_meta": {}, "profiles": {}}
        if _TAXONOMY_FILE.exists():
            data = json.loads(_TAXONOMY_FILE.read_text(encoding="utf-8"))

        profiles = data.setdefault("profiles", {})
        if key in {k.lower() for k in profiles}:
            return False  # уже есть

        profiles[key] = search_terms
        _TAXONOMY_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        # Сбрасываем кеш чтобы новый профиль подхватился
        _load_profiles.cache_clear()
        logger.info(
            "Taxonomy: added profile %r → %s  (total: %d)",
            profile, search_terms, len(profiles),
        )
        return True

    except Exception as exc:
        logger.warning("Taxonomy save_new_profile failed: %s", exc)
        return False
