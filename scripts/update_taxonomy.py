#!/usr/bin/env python3
"""
Скрипт автообновления таксономии вкусов.

Находит вкусы из каталога, не охваченные таксономией, и классифицирует их:
  1. Сначала — быстрые правила по ключевым словам (без LLM, мгновенно)
  2. Затем  — LLM для оставшихся неоднозначных (макс. LLM_BATCH за запуск)

Добавляет найденные вкусы в списки поисковых запросов существующих профилей.

Запуск вручную:
  cd /root/Projects/TBotTabak && .venv_linux/bin/python scripts/update_taxonomy.py

Крон (раз в неделю, понедельник 03:00):
  0 3 * * 1  cd /root/Projects/TBotTabak && .venv_linux/bin/python scripts/update_taxonomy.py >> /var/log/taxonomy_update.log 2>&1
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Максимум LLM-вызовов за один запуск (каждый ~20-30с на CPU)
LLM_BATCH = 10

# ── Ключевые слова → профили ──────────────────────────────────────────────────
# Если ключевое слово СОДЕРЖИТСЯ в названии вкуса → добавляем в эти профили.
# Порядок важен: более конкретные правила идут раньше.
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    # Крепость
    ("берли",        ["крепкий", "берли"]),
    ("бёрли",        ["крепкий", "берли"]),
    ("афгано",       ["крепкий", "табачный"]),
    ("хардкор",      ["крепкий"]),
    ("криолло",      ["крепкий", "табачный"]),
    ("no aroma",     ["без ароматизатора", "табачный"]),
    ("табачн",       ["табачный"]),
    ("чёрн",         ["крепкий", "табачный"]),   # Чёрное на чёрном, Чёрный Афгано

    # Алкоголь
    ("коньяк",       ["алкогольный"]),
    ("куантро",      ["алкогольный"]),
    ("егермейст",    ["алкогольный"]),
    ("рислинг",      ["алкогольный", "вино"]),
    ("виски",        ["алкогольный"]),
    ("текила",       ["алкогольный"]),
    (" ром ",        ["алкогольный"]),
    ("ром,",         ["алкогольный"]),
    ("джин",         ["алкогольный", "джин"]),
    ("вино,",        ["алкогольный", "вино"]),
    ("путешестви",   ["алкогольный"]),  # «Путешествие с вином»
    ("белый русск",  ["алкогольный"]),
    ("мохито",       ["алкогольный", "свежий", "мятный"]),
    ("cuba",         ["алкогольный", "кола"]),     # Cuba Libre
    ("коктейл",      ["алкогольный", "напиток"]), # Коктейль Сингапурский линг
    ("шампанск",     ["алкогольный"]),             # шампанского
    ("стопка",       ["алкогольный"]),             # Стопка

    # Цветочные
    ("жасмин",       ["цветочный", "чайный"]),
    ("лаванд",       ["цветочный"]),
    ("пион",         ["цветочный"]),
    ("сирень",       ["цветочный"]),
    ("сакур",        ["цветочный"]),
    ("бузин",        ["цветочный"]),
    ("роз",          ["цветочный"]),
    ("фиалк",        ["цветочный"]),
    ("нежност",      ["цветочный"]),              # Нежность
    ("одуванч",      ["цветочный"]),              # Одуванчик

    # Ореховые
    ("орех",         ["ореховый"]),
    ("фисташк",      ["ореховый"]),
    ("миндал",       ["ореховый"]),
    ("фундук",       ["ореховый"]),

    # Шоколадные/десертные
    ("шоколад",      ["шоколадный", "десертный"]),
    ("брауни",       ["шоколадный", "десертный"]),
    ("карамел",      ["сладкий", "десертный", "карамель"]),
    ("ваниль",       ["сладкий", "десертный", "ваниль"]),
    ("ванил",        ["сладкий", "десертный"]),
    ("сливочн",      ["сладкий", "сливочный"]),
    ("сливки",       ["сладкий", "сливочный"]),
    ("мороженое",    ["десертный", "сладкий"]),
    ("пирог",        ["десертный"]),
    ("вафл",         ["десертный"]),
    ("печень",       ["десертный"]),
    ("бисквит",      ["десертный"]),
    ("йогурт",       ["сладкий"]),
    ("молок",        ["сладкий"]),

    # Конфеты/жвачка
    ("жвачк",        ["жвачка", "сладкий"]),
    ("жевател",      ["жвачка"]),
    ("мармелад",     ["конфеты", "мармелад"]),
    ("холс",         ["конфеты", "леденцы"]),
    ("скитлс",       ["конфеты"]),
    ("swittles",     ["конфеты"]),
    ("конфет",       ["конфеты"]),
    ("леденц",       ["конфеты", "леденцы"]),
    ("попкорн",      ["десертный"]),
    ("крекер",       ["десертный"]),              # Крекер
    ("куки",         ["десертный"]),              # Куки Монстр
    ("хлопья",       ["десертный"]),              # Те самые хлопья для завтрака
    ("сорбет",       ["ягодный", "сладкий", "десертный"]),  # Красный сорбет

    # Напитки
    ("кола",         ["кола", "напиток"]),
    ("энергетик",    ["энергетик", "напиток"]),
    ("лимонад",      ["напиток", "цитрусовый"]),
    ("содов",        ["напиток"]),
    ("спрайт",       ["напиток"]),
    ("чай",          ["чайный", "напиток"]),
    ("тоник",        ["напиток", "свежий"]),      # Сибирский Тоник
    ("тархун",       ["напиток", "свежий"]),      # Тархун (советский лимонад)
    ("кардамон",     ["чайный", "напиток"]),      # Кардамон

    # Свежесть/ментол
    ("мят",          ["мятный", "свежий"]),       # мяты, мятой, мятного (все формы)
    ("мята",         ["мятный", "свежий"]),
    ("мятн",         ["мятный", "свежий"]),
    ("холодок",      ["свежий", "холодок"]),
    ("холод",        ["свежий", "холодок"]),
    ("зимн",         ["свежий"]),
    ("арктич",       ["свежий"]),
    ("эвкалипт",     ["свежий"]),
    ("льда",         ["свежий", "лёд"]),
    ("крио",         ["свежий", "холодок", "лёд"]),  # Крио
    ("пихт",         ["свежий"]),                 # Пихта, Всегда с пихтой

    # Цитрус
    ("лимон",        ["цитрусовый", "лимон"]),
    ("лайм",         ["цитрусовый", "лайм"]),
    ("апельсин",     ["цитрусовый", "апельсин"]),
    ("грейпфрут",    ["цитрусовый", "грейпфрут"]),
    ("мандарин",     ["цитрусовый", "мандарин"]),
    ("цитрус",       ["цитрусовый"]),
    ("orange",       ["цитрусовый", "апельсин"]),  # катушка) - Orange

    # Тропик/экзотика
    ("манго",        ["тропический", "манго"]),
    ("маракуйя",     ["тропический", "маракуйя"]),
    ("маракуйи",     ["тропический", "маракуйя"]),
    ("ананас",       ["тропический", "ананас"]),
    ("кокос",        ["тропический", "кокос"]),
    ("банан",        ["тропический", "банан"]),
    ("папайя",       ["тропический"]),
    ("папайа",       ["тропический"]),
    ("гуава",        ["тропический", "экзотика"]),
    ("кивано",       ["тропический", "экзотика"]),
    ("личи",         ["тропический"]),
    ("мангустин",    ["тропический"]),
    ("тропическ",    ["тропический"]),
    ("экзотич",      ["тропический", "экзотика"]),
    ("фейхоа",       ["тропический", "фейхоа"]),
    ("киви",         ["тропический", "киви"]),
    ("кактус",       ["тропический", "экзотика"]),  # кактуса
    ("джунгл",       ["тропический", "экзотика"]),  # Джунглевый сок
    ("карибск",      ["тропический", "алкогольный"]),  # Карибская ночь
    ("тропикан",     ["тропический"]),             # Тропикано

    # Ягоды
    ("малина",       ["ягодный", "малина"]),
    ("малин",        ["ягодный", "малина"]),
    ("клубник",      ["ягодный", "клубника"]),
    ("клубниц",      ["ягодный", "клубника"]),
    ("черник",       ["ягодный", "черника"]),
    ("голубик",      ["ягодный", "голубика"]),
    ("ежевик",       ["ягодный"]),
    ("смородин",     ["ягодный", "смородина"]),
    ("вишн",         ["ягодный", "вишня"]),
    ("черешн",       ["ягодный", "черешня"]),
    ("земляник",     ["ягодный"]),
    ("барбарис",     ["ягодный", "кислый"]),
    ("ирга",         ["ягодный"]),
    ("шелковиц",     ["ягодный"]),
    ("годжи",        ["ягодный"]),
    ("асаи",         ["ягодный"]),
    ("янгмей",       ["ягодный"]),
    ("ягод",         ["ягодный"]),
    ("клубничк",     ["ягодный", "клубника"]),    # Клубничка 18+
    ("черничн",      ["ягодный", "черника"]),     # свежий черничный вкус с мятой
    ("варенье",      ["сладкий", "ягодный"]),     # клубничное варенье
    ("джойбер",      ["ягодный"]),                # Джойберри
    ("скиттлс",      ["конфеты"]),                # Скиттлс Лёд (double-т spelling)

    # Кислое
    ("кислый",       ["кислый"]),
    ("кислое",       ["кислый"]),
    ("кислых",       ["кислый"]),
    ("кислого",      ["кислый"]),
    ("кисл",         ["кислый"]),

    # Фрукты
    ("персик",       ["фруктовый", "сладкий", "персик"]),
    ("персика",      ["фруктовый", "сладкий", "персик"]),
    ("арбуз",        ["сладкий", "арбуз"]),
    ("дыня",         ["сладкий", "дыня"]),
    ("виноград",     ["фруктовый", "виноград"]),
    ("яблок",        ["фруктовый", "яблоко"]),
    ("яблоч",        ["фруктовый", "яблоко"]),
    ("груш",         ["фруктовый"]),
    ("слив",         ["фруктовый"]),
    ("абрикос",      ["фруктовый", "сладкий"]),
    ("нектарин",     ["фруктовый", "сладкий"]),
    ("облепих",      ["фруктовый", "кислый"]),
    ("фрукт",        ["фруктовый"]),              # фруктового микса, 6 фруктов
]

# ── Подозрительные записи — пропускаем ───────────────────────────────────────
_JUNK_PATTERNS = [
    r"^\d",           # начинается с цифры: "01)", "1915"
    r"^[a-z_]+$",    # только латиница/underscore: "sarma_raspberry", "desvall"
    r"^─+",           # разделители таксономии
    r"^.{1,2}$",     # слишком короткие
]
_JUNK_RE = re.compile("|".join(_JUNK_PATTERNS))


def _is_junk(flavor: str) -> bool:
    return bool(_JUNK_RE.match(flavor.lower().strip()))


def classify_by_rules(flavor: str) -> list[str]:
    """
    Быстрая классификация по ключевым словам.
    Возвращает список профилей (может быть пустым).
    """
    fl = flavor.lower()
    matched: list[str] = []
    seen: set[str] = set()
    for keyword, profiles in KEYWORD_RULES:
        if keyword in fl:
            for p in profiles:
                if p not in seen:
                    seen.add(p)
                    matched.append(p)
    return matched


# ── LLM-классификатор ────────────────────────────────────────────────────────

_LLM_PROMPT = """\
Кальянный табак. Определи к каким профилям относится вкус.

Вкус: «{flavor}»

Профили (выбирай только из них): {profiles}

Верни JSON: {{"profiles": ["проф1", "проф2"]}}
Если не подходит ни один — верни: {{"profiles": []}}
"""


async def classify_by_llm(flavor: str, known_profiles: list[str]) -> list[str]:
    """LLM-классификация для неоднозначных вкусов."""
    from oshisha.llm import _ask, _groq_key
    from oshisha.llm_json import extract_json_object

    profiles_str = ", ".join(known_profiles[:50])
    prompt = _LLM_PROMPT.format(flavor=flavor, profiles=profiles_str)
    try:
        raw = await _ask(
            prompt,
            temperature=0,
            json_mode=bool(_groq_key()),
        )
        data = extract_json_object(raw)
        if data:
            cats = data.get("profiles", [])
            valid_set = set(known_profiles)
            return [c.lower().strip() for c in cats if c.lower().strip() in valid_set][:3]
    except Exception as exc:
        logger.warning("LLM classify failed for %r: %s", flavor, exc)
    return []


async def _classify_batch_parallel(
    flavors: list[str],
    profile_names: list[str],
) -> dict[str, list[str]]:
    """Параллельная LLM-классификация с лимитом одновременных вызовов."""
    sem = asyncio.Semaphore(max(1, int(os.environ.get("TAXONOMY_LLM_PARALLEL", "3"))))
    hits: dict[str, list[str]] = {}

    async def one(flavor: str) -> None:
        async with sem:
            cats = await classify_by_llm(flavor, profile_names)
            if cats:
                for cat in cats:
                    hits.setdefault(cat, []).append(flavor)

    await asyncio.gather(*[one(f) for f in flavors])
    return hits


# ── Основная логика ──────────────────────────────────────────────────────────

def get_uncategorized(profiles: dict[str, list[str]]) -> list[str]:
    """Вкусы из каталога, не встречающиеся ни в одном профиле."""
    from oshisha.vocabulary import get_vocabulary
    v = get_vocabulary()
    all_flavors = {f.display.strip() for f in v.flavors.values() if f.display}

    covered = {t.lower().strip() for terms in profiles.values() for t in terms}
    result = []
    for f in sorted(all_flavors):
        if f.lower().strip() not in covered and not _is_junk(f):
            result.append(f)
    return result


def apply_additions(
    taxonomy_path: Path,
    additions: dict[str, list[str]],
) -> int:
    """
    Добавляет вкусы в существующие профили.
    Возвращает общее число добавленных записей.
    """
    if not additions:
        return 0

    data = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    profiles = data.setdefault("profiles", {})
    total_added = 0

    for profile, new_flavors in sorted(additions.items()):
        if profile not in profiles:
            logger.warning("Профиль %r не найден в файле, пропускаю", profile)
            continue
        existing_lower = {t.lower().strip() for t in profiles[profile]}
        added = 0
        for flavor in new_flavors:
            if flavor.lower().strip() not in existing_lower:
                profiles[profile].append(flavor)
                existing_lower.add(flavor.lower().strip())
                added += 1
        if added:
            logger.info("  %r: +%d → %s", profile, added, new_flavors)
            total_added += added

    if total_added:
        taxonomy_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Сбрасываем кеш модуля
        from oshisha.taxonomy import _load_profiles
        _load_profiles.cache_clear()

    return total_added


async def main() -> None:
    from oshisha.taxonomy import _load_profiles, _TAXONOMY_FILE

    logger.info("=" * 60)
    logger.info("Обновление таксономии вкусов")
    logger.info("=" * 60)

    profiles = _load_profiles()
    # Фильтруем разделители (ключи начинаются с «─»)
    real_profiles = {k: v for k, v in profiles.items() if not k.startswith("─")}
    profile_names = sorted(real_profiles.keys())
    logger.info("Профилей в таксономии: %d", len(profile_names))

    uncategorized = get_uncategorized(real_profiles)
    logger.info("Вкусов без категории: %d", len(uncategorized))

    if not uncategorized:
        logger.info("Всё охвачено — обновление не требуется.")
        return

    # ── Шаг 1: правила по ключевым словам ───────────────────────────────────
    rule_hits: dict[str, list[str]] = {}   # profile → [flavors]
    llm_queue: list[str] = []              # вкусы для LLM

    for flavor in uncategorized:
        matched = classify_by_rules(flavor)
        if matched:
            for prof in matched:
                if prof in set(profile_names):
                    rule_hits.setdefault(prof, []).append(flavor)
        else:
            llm_queue.append(flavor)

    rule_total = sum(len(v) for v in rule_hits.values())
    logger.info("Правила: классифицировано %d вкусов → %d профилей",
                len(uncategorized) - len(llm_queue), len(rule_hits))
    logger.info("Не охвачено правилами: %d (из них LLM обработает до %d)",
                len(llm_queue), LLM_BATCH)

    # Применяем результаты правил
    if rule_hits:
        logger.info("Применяю правила...")
        added = apply_additions(_TAXONOMY_FILE, rule_hits)
        logger.info("Добавлено через правила: %d записей", added)

    # ── Шаг 2: LLM для неоднозначных ────────────────────────────────────────
    if llm_queue and LLM_BATCH > 0:
        batch = llm_queue[:LLM_BATCH]
        logger.info("LLM: обрабатываю %d вкусов из %d...", len(batch), len(llm_queue))

        logger.info("LLM: parallel classify (max %s at once)...", os.environ.get("TAXONOMY_LLM_PARALLEL", "3"))
        llm_hits = await _classify_batch_parallel(batch, profile_names)
        for flavor in batch:
            assigned = [p for p, fs in llm_hits.items() if flavor in fs]
            if assigned:
                logger.info("  %r → %s", flavor, assigned)
            else:
                logger.info("  %r → не классифицирован", flavor)

        if llm_hits:
            added_llm = apply_additions(_TAXONOMY_FILE, llm_hits)
            logger.info("Добавлено через LLM: %d записей", added_llm)

        remaining = len(llm_queue) - len(batch)
        if remaining > 0:
            logger.info(
                "Осталось неохваченных (следующий запуск): %d", remaining
            )

    # ── Итог ─────────────────────────────────────────────────────────────────
    # Считаем финальное покрытие
    from oshisha.taxonomy import _load_profiles as lp
    lp.cache_clear()
    final_profiles = lp()
    covered_after = {t.lower().strip() for terms in final_profiles.values() for t in terms}

    from oshisha.vocabulary import get_vocabulary
    v = get_vocabulary()
    all_flavors = {f.display.lower().strip() for f in v.flavors.values() if f.display}
    still_uncovered = [
        f for f in all_flavors
        if f not in covered_after and not _is_junk(f)
    ]

    logger.info("=" * 60)
    logger.info("Итог: в каталоге %d вкусов", len(all_flavors))
    logger.info("      охвачено таксономией: %d", len(all_flavors) - len(still_uncovered))
    logger.info("      не охвачено: %d", len(still_uncovered))
    if still_uncovered:
        logger.info("Примеры неохваченных: %s",
                    ", ".join(repr(f) for f in still_uncovered[:10]))
    logger.info("=" * 60)

    from oshisha import catalog_cache
    from oshisha.llm import invalidate_vocab_cache

    invalidate_vocab_cache()
    catalog_cache.invalidate()
    logger.info("Сброшены кеши vocabulary, catalog snapshot (при следующем запуске бота — прогрев)")


if __name__ == "__main__":
    asyncio.run(main())
