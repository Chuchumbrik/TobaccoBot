"""Парсинг исключений из пользовательского запроса и фильтрация хитов.

Поддерживаемые формы исключений:
  без Адалии          без мяты
  кроме BlackBurn     исключить ваниль
  не использовать бренд Adalya
  не хочу манго       убрать Duft

Особенность: русские бренды ("Адалии") и английские ("Adalya") унифицируются
через транслитерацию + prefix-сравнение (первые 4 символа).
"""

from __future__ import annotations

import re

# ── Паттерн для извлечения исключений ────────────────────────────────────────

# Захватывает 1 слово, или 2 слова если второе начинается с заглавной буквы
# (чтобы взять "Al Fakher", но НЕ "Адалии ягодный")
_EXCL_RE = re.compile(
    r'(?i)\b(?:'
    r'без\s+(?:бренда?\s+)?'
    r'|кроме\s+(?:бренда?\s+)?'
    r'|не\s+(?:использовать|используй|использую|хочу|надо|нужно|нравится|нравятся)\s+(?:бренд[а-яё]?\s+)?'
    r'|исключ(?:ить|и|а|ая)\s+(?:бренда?\s+)?'
    r'|убрать\s+(?:бренд[а-яё]?\s+)?'
    r')'
    r'([A-Za-zА-Яа-яёЁ][A-Za-zА-Яа-яёЁ\'\"]*'
    r'(?:\s+(?-i:[A-ZА-ЯЁ])[A-Za-zА-Яа-яёЁ\'\"]*)?)'  # второе слово только с заглавной (case-sensitive!)
)

# Базовая транслитерация кириллица → латиница (33 символа → 33 символа)
_CYR2LAT = str.maketrans(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
    "abvgdeyozijklmnoprstufkccssyyeyua"
)

# Многосимвольные замены: ч→ch, ш→sh, щ→shch, ж→zh, ц→ts, х→kh и т.д.
# Используются дополнительно к _CYR2LAT для лучшего совпадения с реальными
# написаниями брендов (Chabacco, Shisha, Zhara и пр.)
# ф→ph: нужно для греческих/английских заимствований: «сапфир» → «saphir»,
#        prefix «saph» vs «sapphire» (не совпадает по 4 символам, но совпадает
#        по 3: «sap») — потому ниже добавлен 3-симв. fallback.
_CYR_MULTI: dict[str, str] = {
    "ж": "zh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ё": "yo",
    "ю": "yu",
    "я": "ya",
    "й": "y",
    "х": "kh",
    "ф": "ph",   # сапфир→saphir; f-вариант уже есть в _CYR2LAT
}

# Минимальная длина префикса для нечёткого brand-матчинга
_PREFIX_LEN = 4


def _translit(s: str) -> str:
    return s.lower().translate(_CYR2LAT)


def _translit_proper(s: str) -> str:
    """Транслитерация с многосимвольными заменами: ч→ch, ш→sh, щ→shch…

    Используется параллельно с _translit() чтобы охватить и однобуквенные,
    и многобуквенные схемы транслитерации брендов.
    """
    result: list[str] = []
    for c in s.lower():
        if c in _CYR_MULTI:
            result.append(_CYR_MULTI[c])
        else:
            result.append(c.translate(_CYR2LAT))
    return "".join(result)


def parse_exclusions(text: str) -> tuple[str, list[str]]:
    """Извлекает исключения из текста, возвращает (очищенный_текст, [термины]).

    Примеры:
      "кислый без Адалии"            → ("кислый", ["Адалии"])
      "ягодный, кроме BlackBurn"     → ("ягодный", ["BlackBurn"])
      "микс без мяты и без манго"    → ("микс и", ["мяты", "манго"])
      "не использовать бренд Duft"   → ("", ["Duft"])
      "Al Fakher исключить ваниль"   → ("Al Fakher", ["ваниль"])
      "без бренда Al Fakher"         → ("", ["Al Fakher"])
    """
    excluded: list[str] = []
    spans: list[tuple[int, int]] = []

    for m in _EXCL_RE.finditer(text):
        term = m.group(1).strip()
        if term:
            excluded.append(term)
            spans.append((m.start(), m.end()))

    # Удаляем совпавшие фрагменты из текста (с конца, чтобы не сбить индексы)
    cleaned = text
    for start, end in reversed(spans):
        cleaned = cleaned[:start] + cleaned[end:]
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip(" ,;")

    return cleaned, excluded


def _stem(s: str, n: int = 5) -> str:
    """Первые n символов — работает как нечёткий стем для падежей."""
    return s.lower()[:n]


def hit_excluded(hit, excluded_terms: list[str]) -> bool:
    """True если продукт/бренд совпадает хотя бы с одним исключением.

    Стратегия матчинга (от строгого к мягкому):
    1. Полное вхождение оригинала в имя или бренд (для английских слов)
    2. Полное вхождение транслита в имя/бренд (для ингредиентов: "манго" → "mango")
    3. Stem (5 букв) оригинала в имя/бренд
    4. Prefix-match транслитераций (≥4 символов) — для склонений:
       «Адалии» → translit «adalii», brand «Adalya» → «adalya»,
       оба начинаются с «adal» → совпадение.
    """
    if not excluded_terms:
        return False

    name  = (getattr(hit.product, 'name', '') or '').lower()
    brand = (getattr(hit, 'brand_display', '') or '').lower()

    name_tl  = _translit(name)
    brand_tl = _translit(brand)

    for term in excluded_terms:
        t    = term.lower()
        t_tl = _translit(t)
        t_tl_p = _translit_proper(t)  # многосимвольная схема: ч→ch, ш→sh…

        # 1. Полное вхождение оригинала
        if t in name or t in brand:
            return True
        # 2. Полное вхождение транслита (ключевой случай: "манго"→"mango" в "mango tango")
        if t_tl in name_tl or t_tl in brand_tl:
            return True
        # 2b. То же самое с многосимвольной транслитерацией
        #     "чабако"→"chabako" vs "chabacco": stem/prefix матч ниже
        if t_tl_p in name_tl or t_tl_p in brand_tl:
            return True
        # 3. Stem оригинала (5 букв) для кириллических брендов/ингредиентов
        t_stem = _stem(t)
        if t_stem in name or t_stem in brand:
            return True
        # 4. Prefix-match транслитераций — для склонений брендов
        #    «Адалии» → «adalii»[:4]="adal", brand «Adalya» → «adalya»[:4]="adal" ✓
        #    «чабако» → однобукв: "caba"[:4], многобукв: "chab"[:4]
        #    brand «Chabacco» → «chabacco»[:4]="chab" → совпадение через t_tl_p ✓
        for tl in (t_tl, t_tl_p):
            if len(tl) >= _PREFIX_LEN:
                pfx = tl[:_PREFIX_LEN]
                if brand_tl.startswith(pfx) or name_tl.startswith(pfx):
                    return True
                for word in name_tl.split():
                    if word.startswith(pfx):
                        return True

        # 5. Кириллические падежные окончания: «мяты» → «мят» совпадёт в «мята»
        #    Применяем только если термин оканчивается на кириллицу и достаточно длинный.
        if t and 'Ѐ' <= t[-1] <= 'ӿ' and len(t) >= 4:
            t_short = t[:-1]  # убираем одну букву (падежное окончание)
            if t_short in name or t_short in brand:
                return True


    return False


def filter_hits(hits: list, excluded_terms: list[str]) -> list:
    """Возвращает хиты, не попадающие под исключения."""
    if not excluded_terms:
        return hits
    kept = [h for h in hits if not hit_excluded(h, excluded_terms)]
    return kept
