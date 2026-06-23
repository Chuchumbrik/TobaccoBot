"""Генерация и применение патчей словаря таксономии.

Два механизма самообучения:
  1А. Конверсионный сигнал: обнаруживаем удачные поиски (→ cart_add) и
      переформулировки (search → search в течение 5 минут).
  2Г. Генерация патча: LLM предлагает конкретные ключи для добавления в taxonomy,
      сохраняет в data/vocab/patches/YYYY-MM-DD.json.
      Применяется командой /apply_vocab_patch.

Формат патча:
    {
      "date": "2026-05-26",
      "generated_at": "2026-05-26T03:05:23",
      "stats": {"zero_result": 7, "unknown_terms": 4, "converted": 3, "reformulated": 2},
      "summary": "Добавить 3 профиля: дымное, летнее, нурр-табак",
      "add": {
        "дымное":   ["dark leaf", "virginia dark", "трубочный"],
        "летнее":   ["арбуз", "лимонад", "персик лёгкий"]
      },
      "synonyms": {
        "дымок": "дымное"
      }
    }
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from oshisha.llm import _ask, extract_json_object  # noqa: WPS450

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = ROOT / "data" / "vocab" / "flavor_taxonomy.json"
PATCHES_DIR   = ROOT / "data" / "vocab" / "patches"

CONVERSION_WINDOW_MIN   = 30  # поиск → cart_add: считаем конверсией
REFORMULATION_WINDOW_MIN = 5  # поиск → поиск: считаем переформулировкой

MAX_PATCH_KEYS = 6  # не больше N новых ключей в одном патче


# ── 1А: анализ конверсионного сигнала ────────────────────────────────────────

def _parse_ts(ts_str: str) -> datetime | None:
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def analyze_conversions(entries: list[dict]) -> dict:
    """Анализирует user_logs и возвращает сигналы об успешности поиска.

    Returns:
        {
          "successful": [{"query": ..., "intent": ..., "user": ...}],
          "reformulated": [{"first": ..., "second": ..., "user": ...}],
        }
    """
    search_intents = {"flavor_search", "advise", "mix", "theme"}

    # Группируем Q-записи по user_id, сохраняем порядок
    by_user: dict = defaultdict(list)
    for e in entries:
        if e.get("type") == "Q":
            uid = e.get("user_id")
            if uid:
                by_user[uid].append(e)

    successful: list[dict] = []
    reformulated: list[dict] = []

    for uid, user_entries in by_user.items():
        for i, e in enumerate(user_entries):
            intent = e.get("intent", "")
            ts = _parse_ts(e.get("ts", ""))
            if not ts:
                continue

            # ── Конверсия: search → cart_add ─────────────────────────────────
            if intent in search_intents:
                for j in range(i + 1, len(user_entries)):
                    nxt = user_entries[j]
                    nxt_ts = _parse_ts(nxt.get("ts", ""))
                    if not nxt_ts:
                        continue
                    delta = (nxt_ts - ts).total_seconds() / 60
                    if delta > CONVERSION_WINDOW_MIN:
                        break
                    if nxt.get("intent") == "cart_add":
                        successful.append({
                            "query": e.get("text", ""),
                            "intent": intent,
                            "user": e.get("username") or str(uid),
                        })
                        break

            # ── Переформулировка: flavor_search → flavor_search ──────────────
            if intent == "flavor_search":
                for j in range(i + 1, len(user_entries)):
                    nxt = user_entries[j]
                    if nxt.get("intent") != "flavor_search":
                        continue
                    nxt_ts = _parse_ts(nxt.get("ts", ""))
                    if not nxt_ts:
                        continue
                    delta = (nxt_ts - ts).total_seconds() / 60
                    if delta > REFORMULATION_WINDOW_MIN:
                        break
                    reformulated.append({
                        "first":  e.get("text", ""),
                        "second": nxt.get("text", ""),
                        "user":   e.get("username") or str(uid),
                    })
                    break

    return {"successful": successful, "reformulated": reformulated}


# ── 2Г: генерация JSON-патча ──────────────────────────────────────────────────

_PATCH_SYSTEM = """\
Ты улучшаешь словарь поиска кальянного табака. Словарь — это ключи (слова/фразы),
к которым привязаны поисковые термины из каталога.
Когда пользователь пишет ключ — бот ищет по привязанным терминам.

ПРАВИЛА поисковых терминов:
- Описательные вкусы/ощущения, которые встречаются в НАЗВАНИЯХ табаков в каталоге
- Примеры: "грейпфрут", "ваниль сливочная", "кислых ягод", "арбуз холодный"
- НЕ бренды (не Al Fakher, Adalya и т.п.)
- 2–5 терминов на ключ

Выведи ТОЛЬКО валидный JSON, без пояснений:\
"""

_PATCH_SCHEMA = """\
{
  "summary": "одна строка — что добавляется и зачем",
  "add": {
    "ключ": ["термин1", "термин2", "термин3"]
  },
  "synonyms": {
    "новый_ключ": "существующий_ключ_в_словаре"
  }
}
Максимум 6 ключей в "add" + "synonyms" суммарно. Только то, в чём уверен.\
"""


def _build_patch_prompt(
    zero_results: list[tuple[str, int]],
    unknown_terms: list[str],
    taxonomy_keys: list[str],
    conversions: dict,
) -> str:
    lines: list[str] = []

    if zero_results:
        lines.append("Запросы без результатов:")
        for q, cnt in zero_results[:20]:
            lines.append(f"  {cnt}x \"{q}\"")

    if unknown_terms:
        lines.append(f"\nСлова из запросов, которых нет в словаре:")
        lines.append("  " + ", ".join(unknown_terms[:25]))

    if conversions.get("reformulated"):
        lines.append("\nПользователи переформулировали запрос (первый не сработал):")
        for r in conversions["reformulated"][:8]:
            lines.append(f'  "{r["first"]}" → "{r["second"]}"')

    if conversions.get("successful"):
        lines.append("\nЗапросы, после которых добавили в корзину (эти работают!):")
        for s in conversions["successful"][:8]:
            lines.append(f'  [{s["intent"]}] "{s["query"]}"')

    lines.append(f"\nТекущие ключи словаря (первые 80):")
    lines.append(", ".join(taxonomy_keys[:80]))

    lines.append(f"\n\nВыведи JSON по схеме:\n{_PATCH_SCHEMA}")

    return "\n".join(lines)


def _load_taxonomy_keys() -> list[str]:
    """Возвращает список ключей таксономии (без секционных разделителей)."""
    try:
        data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        profiles = data.get("profiles", data)
        return [k for k in profiles if not k.startswith("──")]
    except Exception:
        return []


async def generate_vocab_patch(
    entries_today: list[dict],
    zero_results: list[tuple[str, int]],
    unknown_terms: list[str],
) -> dict | None:
    """Генерирует JSON-патч через LLM. Возвращает dict или None если нечего добавить."""
    conversions = analyze_conversions(entries_today)
    taxonomy_keys = _load_taxonomy_keys()

    # Нет сигналов — нет смысла гонять LLM
    total_signals = (
        len(zero_results) + len(unknown_terms)
        + len(conversions["reformulated"])
    )
    if total_signals == 0:
        logger.info("vocab_patch: нет сигналов для генерации патча")
        return None

    prompt = _build_patch_prompt(zero_results, unknown_terms, taxonomy_keys, conversions)

    logger.info(
        "vocab_patch: генерируем патч (%d zero-results, %d unknown, "
        "%d reformulated, %d converted)",
        len(zero_results), len(unknown_terms),
        len(conversions["reformulated"]), len(conversions["successful"]),
    )

    try:
        raw = await _ask(
            prompt,
            system=_PATCH_SYSTEM,
            temperature=0.2,
            max_tokens=800,
            json_mode=True,
        )
        patch_data = extract_json_object(raw)
    except Exception as exc:
        logger.error("vocab_patch: LLM вернул ошибку: %s", exc)
        return None

    if not patch_data or not (patch_data.get("add") or patch_data.get("synonyms")):
        logger.info("vocab_patch: LLM не предложил изменений")
        return None

    # Убираем ключи которые уже есть в таксономии
    existing = set(_load_taxonomy_keys())
    patch_data["add"] = {
        k: v for k, v in patch_data.get("add", {}).items()
        if k not in existing and isinstance(v, list) and v
    }
    patch_data["synonyms"] = {
        k: v for k, v in patch_data.get("synonyms", {}).items()
        if k not in existing and isinstance(v, str)
    }

    # Проверка лимита
    total_keys = len(patch_data["add"]) + len(patch_data["synonyms"])
    if total_keys == 0:
        logger.info("vocab_patch: все предложенные ключи уже есть в таксономии")
        return None
    if total_keys > MAX_PATCH_KEYS:
        # Обрезаем до лимита
        add_items = list(patch_data["add"].items())[:MAX_PATCH_KEYS]
        patch_data["add"] = dict(add_items)
        patch_data["synonyms"] = {}

    # Добавляем метаданные
    today = entries_today[0]["ts"][:10] if entries_today else datetime.now().strftime("%Y-%m-%d")
    patch_data["date"] = today
    patch_data["generated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    patch_data["stats"] = {
        "zero_result":   len(zero_results),
        "unknown_terms": len(unknown_terms),
        "converted":     len(conversions["successful"]),
        "reformulated":  len(conversions["reformulated"]),
    }

    return patch_data


# ── Сохранение / загрузка патча ───────────────────────────────────────────────

def save_patch(patch: dict, date: str | None = None) -> Path:
    """Сохраняет патч в data/vocab/patches/YYYY-MM-DD.json."""
    PATCHES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{date or patch.get('date', datetime.now().strftime('%Y-%m-%d'))}.json"
    path = PATCHES_DIR / filename
    path.write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("vocab_patch: сохранён в %s", path)
    return path


def load_latest_patch() -> tuple[Path | None, dict | None]:
    """Загружает самый свежий патч. Возвращает (path, patch) или (None, None)."""
    if not PATCHES_DIR.exists():
        return None, None
    files = sorted(PATCHES_DIR.glob("*.json"), reverse=True)
    if not files:
        return None, None
    path = files[0]
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("vocab_patch: ошибка чтения %s: %s", path, exc)
        return None, None


def load_patch_by_date(date: str) -> tuple[Path | None, dict | None]:
    path = PATCHES_DIR / f"{date}.json"
    if not path.exists():
        return None, None
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("vocab_patch: ошибка чтения %s: %s", path, exc)
        return None, None


# ── Применение патча к таксономии ────────────────────────────────────────────

def apply_patch(patch: dict) -> dict:
    """Применяет патч к flavor_taxonomy.json.

    Returns:
        {"added": [...], "skipped": [...], "errors": [...]}
    """
    try:
        raw = TAXONOMY_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception as exc:
        return {"added": [], "skipped": [], "errors": [f"Не удалось прочитать taxonomy: {exc}"]}

    profiles = data.get("profiles", data)
    existing_keys = set(profiles.keys())

    added:   list[str] = []
    skipped: list[str] = []
    errors:  list[str] = []

    # ── Добавляем новые профили ───────────────────────────────────────────────
    for key, terms in patch.get("add", {}).items():
        if key in existing_keys:
            skipped.append(key)
            continue
        if not isinstance(terms, list) or not terms:
            errors.append(f"{key!r}: пустой список терминов")
            continue
        profiles[key] = [str(t) for t in terms]
        added.append(key)

    # ── Синонимы: новый ключ → те же термины что у существующего ─────────────
    for new_key, ref_key in patch.get("synonyms", {}).items():
        if new_key in existing_keys:
            skipped.append(new_key)
            continue
        if ref_key not in profiles:
            errors.append(f"{new_key!r} → {ref_key!r}: ключ-источник не найден")
            continue
        profiles[new_key] = profiles[ref_key]
        added.append(new_key)

    # ── Сохраняем обратно ─────────────────────────────────────────────────────
    if added:
        if "profiles" in data:
            data["profiles"] = profiles
        else:
            data = profiles
        try:
            TAXONOMY_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("vocab_patch: применён, добавлено %d ключей: %s", len(added), added)
        except Exception as exc:
            errors.append(f"Ошибка записи файла: {exc}")
            added.clear()

    return {"added": added, "skipped": skipped, "errors": errors}


# ── Форматирование превью патча для Telegram ──────────────────────────────────

def format_patch_preview(patch: dict) -> str:
    """Возвращает Telegram HTML-превью патча для отправки администратору."""
    date = patch.get("date", "?")
    summary = patch.get("summary", "")
    stats = patch.get("stats", {})
    adds = patch.get("add", {})
    syns = patch.get("synonyms", {})

    lines: list[str] = [
        f"🧩 <b>Vocab-патч {date}</b>",
    ]
    if summary:
        lines.append(f"<i>{summary}</i>")

    if stats:
        parts = []
        if stats.get("zero_result"):
            parts.append(f"{stats['zero_result']} провальных запросов")
        if stats.get("reformulated"):
            parts.append(f"{stats['reformulated']} переформулировок")
        if stats.get("converted"):
            parts.append(f"{stats['converted']} конверсий")
        if parts:
            lines.append(f"<i>Данные: {', '.join(parts)}</i>")

    if adds:
        lines.append("")
        lines.append("<b>➕ Добавить ключи:</b>")
        for key, terms in adds.items():
            terms_str = ", ".join(str(t) for t in terms[:4])
            lines.append(f"  • <code>{key}</code> → {terms_str}")

    if syns:
        lines.append("")
        lines.append("<b>🔗 Синонимы:</b>")
        for new_k, ref_k in syns.items():
            lines.append(f"  • <code>{new_k}</code> = <code>{ref_k}</code>")

    total = len(adds) + len(syns)
    lines.append("")
    lines.append(
        f"Итого: <b>{total} ключей</b>. "
        f"Применить: /apply_vocab_patch или /apply_vocab_patch {date}"
    )

    return "\n".join(lines)
