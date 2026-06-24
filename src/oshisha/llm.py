"""Интеграция с LLM для улучшения поиска.

Бэкенды (.env): Groq (GROQ_API_KEY) или Ollama (локально).
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx

from oshisha.llm_json import extract_json_array, extract_json_object
from oshisha.vocabulary import normalize_text

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_LEARNED_FILE = Path(__file__).resolve().parents[2] / "data" / "vocab" / "llm_learned.json"
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "data" / "prompts"

# ── Загрузка промптов из файлов (с fallback на встроенные константы) ─────────

_prompt_cache: dict[str, str] = {}


def _get_prompt(name: str, fallback: str) -> str:
    """Вернуть системный промпт: сначала из data/prompts/{name}.txt, иначе fallback.

    Результат кэшируется до вызова reload_prompts().
    """
    if name in _prompt_cache:
        return _prompt_cache[name]
    path = _PROMPTS_DIR / f"{name}.txt"
    if path.exists():
        try:
            content = path.read_text(encoding="utf-8").rstrip("\n")
            _prompt_cache[name] = content
            logger.debug("Loaded prompt %r from %s", name, path)
            return content
        except Exception as exc:
            logger.warning("Failed to load prompt %r from file: %s — using fallback", name, exc)
    _prompt_cache[name] = fallback
    return fallback


def reload_prompts() -> int:
    """Перезагрузить все промпты из data/prompts/*.txt.

    Сбрасывает кэш и немедленно читает все .txt-файлы.
    Возвращает количество успешно загруженных промптов.
    Вызывается из /update_taxonomy или /reload_prompts (только для администраторов).
    """
    _prompt_cache.clear()
    loaded = 0
    if not _PROMPTS_DIR.exists():
        logger.warning("reload_prompts: %s не существует", _PROMPTS_DIR)
        return 0
    for path in sorted(_PROMPTS_DIR.glob("*.txt")):
        try:
            _prompt_cache[path.stem] = path.read_text(encoding="utf-8").rstrip("\n")
            loaded += 1
        except Exception as exc:
            logger.warning("reload_prompts: не удалось прочитать %s: %s", path.name, exc)
    logger.info("reload_prompts: загружено %d промптов из %s", loaded, _PROMPTS_DIR)
    return loaded

_http_client: httpx.AsyncClient | None = None
_llm_trace: contextvars.ContextVar[LlmMetrics | None] = contextvars.ContextVar(
    "llm_trace", default=None
)
_llm_hourly_hook: contextvars.ContextVar[Callable[[int], None] | None] = (
    contextvars.ContextVar("llm_hourly_hook", default=None)
)


def set_hourly_quota_hook(hook: Callable[[int], None] | None) -> None:
    _llm_hourly_hook.set(hook)


def _groq_key() -> str:
    return os.environ.get("GROQ_API_KEY", "")


def _groq_model() -> str:
    return os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")


def _ollama_url() -> str:
    return os.environ.get("OLLAMA_URL", "http://localhost:11434")


def _ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")


def _llm_timeout() -> float:
    return float(os.environ.get("LLM_TIMEOUT", "20"))


def _llm_max_parallel() -> int:
    return max(1, int(os.environ.get("LLM_MAX_PARALLEL", "3")))


def backend_name() -> str:
    return f"groq:{_groq_model()}" if _groq_key() else f"ollama:{_ollama_model()}"


@dataclass
class LlmMetrics:
    backend: str = ""
    calls: int = 0
    total_ms: int = 0
    errors: int = 0


def start_llm_trace() -> LlmMetrics:
    m = LlmMetrics(backend=backend_name())
    _llm_trace.set(m)
    return m


def get_llm_trace() -> LlmMetrics | None:
    return _llm_trace.get()


def _record_llm_call(ms: int, *, error: bool = False) -> None:
    trace = _llm_trace.get()
    if trace is not None:
        trace.calls += 1
        trace.total_ms += ms
        if error:
            trace.errors += 1
    if not error:
        hook = _llm_hourly_hook.get()
        if hook is not None:
            hook(1)


async def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(timeout=_llm_timeout())
    return _http_client


async def close_llm_client() -> None:
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None


# ── Промпты ───────────────────────────────────────────────────────────────────

_NORMALIZE_SYSTEM = """\
You are a search query normalizer for a Russian hookah tobacco catalog.
Convert messy user input into clean catalog search terms. Output JSON only.

CATALOG BRANDS (use exact spelling): {brands}
CATALOG FLAVORS (use exact spelling): {flavors}

RULES:
1. Fix typos and Cyrillic transliterations → map to nearest catalog brand/flavor
2. Convert descriptive phrases → specific catalog flavor terms (1-3 words)
3. Brand names → use exact spelling from BRANDS list
4. Already correct terms → return unchanged
5. Return 1-3 search terms max; prefer fewer, more precise
6. NEVER invent brands or flavors absent from the lists
7. Unrecognizable input → return cleaned original

DISAMBIGUATION: check BRANDS first, then FLAVORS for ambiguous words.

EXAMPLES:
Input: "клубнека"
{{"terms":["клубника"],"confidence":"high"}}

Input: "ббёрн айс"
{{"terms":["BlackBurn Ice"],"confidence":"high"}}

Input: "что-то кисленькое с малиной"
{{"terms":["малина кислое","кислая ягода"],"confidence":"medium"}}

Input: "адаля манго"
{{"terms":["Adalya Mango"],"confidence":"high"}}

Input: "дубль"
{{"terms":["Double Apple"],"confidence":"medium"}}

Input: "свежее без ментола"
{{"terms":["фруктовый свежий","арбуз"],"confidence":"low"}}

Input: "малина"
{{"terms":["малина"],"confidence":"high"}}\
"""

_NORMALIZE_USER = 'Input: "{query}"\nOutput JSON (terms array + confidence high/medium/low):'

# ── Советник: искать или уточнить ─────────────────────────────────────────────

_CLARIFY_SYSTEM = """\
You are a tobacco flavor advisor. Decide: search catalog now, or ask user one question.
Output JSON only. No prose.

SEARCH IMMEDIATELY if request contains ANY of:
✓ flavor/taste word (sweet, sour, fruity, fresh, любое описание вкуса)
✓ brand name
✓ mood/occasion ("на вечер", "для расслабления", "в компанию")
✓ any fruit, drink, food, or sensation

ASK QUESTION only if:
✗ Purely abstract ("хочу что-нибудь", "посоветуй", "не знаю")
✗ Only quantity ("два вкуса", "что-то одно")

QUESTION RULES (when asking):
- Max 1 question, under 90 chars, informal Russian
- Focus on: sweet/sour/fresh/strong — most decisive axis
- Offer 2-3 short options when possible
- BAD: "Расскажи подробнее" GOOD: "Сладкое или свежее/кислое?"

CATALOG FLAVORS for query terms: {flavors_sample}

EXAMPLES:
Input: "хочу что-то кисленькое и ягодное"
{{"type":"search","queries":["кислая ягода","малина кислое","смородина"]}}

Input: "посоветуй что-нибудь"
{{"type":"question","question":"Что предпочитаешь — сладкое/фруктовое или свежее/холодное?","options":["Сладкое, фруктовое","Свежее, холодное","Кислое, бодрящее"]}}

Input: "хочу как лимонад"
{{"type":"search","queries":["лимон газировка","цитрус кислый","лимонад"]}}

Input: "хочу два вкуса"
{{"type":"question","question":"Какое направление? Сладкое, кислое, свежее или фруктовое?","options":["Сладкое","Кислое","Свежее","Фруктовое"]}}

Input: "что-то для вечеринки"
{{"type":"search","queries":["тропический","энергетик","фруктовый яркий"]}}

Input: "Adalya что-нибудь"
{{"type":"search","queries":["Adalya","Adalya Love 66"]}}

Input: "хочу"
{{"type":"question","question":"Расскажи что хочется — фрукты, холодок, сладкое или кислое?","options":["Фрукты","Холодок / мята","Сладкое","Кислое"]}}\
"""

_CLARIFY_USER = 'User: "{description}"\nJSON:'

# ── Миксы ─────────────────────────────────────────────────────────────────────

# Важно: этот промпт НЕ использует .format(), поэтому {} без экранирования
_MIX_SYSTEM = """\
You are a master hookah tobacco blender. Create 3 mix recipes for a given flavor direction.
Output JSON object only. No prose.

FLAVOR PAIRING RULES — apply all:
• Balance: each mix needs contrast (sweet+sour, fresh+rich, light+bold)
• Menthol/mint: max 1 component per mix, amplifies everything — use sparingly
• Acid rule: citrus/sour pairs WITH berries/tropical/watermelon — NOT with cream/vanilla
• Rich rule: cream/vanilla/chocolate pairs WITH berry/caramel/coffee — NOT with citrus
• Family rule: no two components from same taste family (e.g. лимон+грейпфрут = forbidden)
• Diversity: no component repeated across the 3 mixes

MIX NAMING RULES:
• Name must EVOKE the experience, not describe ingredients (2-4 Russian words)
• Use: seasons, places, moods, cocktails, metaphors
• GOOD: "Морской бриз", "Закат в Барселоне", "Ночной эспрессо", "Малибу"
• BAD: "Ягодный микс", "Кислый фрукт", "Микс 1"

COMPONENT FORMAT:
• Short flavor profiles (1-3 words) used as catalog search terms
• NOT brand names — taste descriptors only
• 2-3 components per mix

OUTPUT FORMAT — always wrap mixes in object:
{"mixes": [{"name":"...","components":["...","..."],"mood":"..."}, ...]}

EXAMPLES:
Direction: "кислый ягодный"
{"mixes":[{"name":"Дикие ягоды","components":["смородина кислая","персик","холодок"],"mood":"летний"},{"name":"Розовый закат","components":["малина","лимон","арбуз"],"mood":"освежающий"},{"name":"Лесная поляна","components":["ежевика","виноград кислый","мята лёгкая"],"mood":"свежий"}]}

Direction: "выпечка сладкая"
{"mixes":[{"name":"Венское утро","components":["ваниль","карамель","яблоко"],"mood":"уютный"},{"name":"Горячий шоколад","components":["шоколад","сливки","орех"],"mood":"насыщенный"},{"name":"Кофейный кекс","components":["кофе","ваниль","карамель"],"mood":"бодрящий"}]}

Direction: "тропик энергия"
{"mixes":[{"name":"Рио де Жанейро","components":["манго","маракуйя","энергетик"],"mood":"яркий"},{"name":"Гавайский шторм","components":["ананас кислый","кокос","мята"],"mood":"освежающий"},{"name":"Карибский бриз","components":["папайя","гуава","цитрус"],"mood":"экзотика"}]}\
"""

_MIX_USER = 'Flavor direction: "{flavor_intent}"\nJSON object with mixes array:'

# ── Уточнение подборки ────────────────────────────────────────────────────────

_REFINE_SYSTEM = """\
You are a search query optimizer for a hookah tobacco catalog.
User has results but wants to refine them. Output JSON only.

DETECT REFINEMENT TYPE:
• ADD — user wants more of something ("покислее", "добавь мяты")
  → add new terms, keep original direction
• EXCLUDE — user doesn't want something ("без мяты", "не Adalya")
  → shift away, fill excluded_terms
• PIVOT — user wants something different ("нет, лучше тропическое")
  → replace queries completely
• NARROW — user is more specific ("именно клубника, не просто ягода")
  → use narrower terms

CATALOG FLAVORS: {flavors}

EXAMPLES:
Original: "ягодное", Refinement: "хочу покислее"
{{"type":"ADD","queries":["кислая ягода","смородина","малина кислое"],"excluded_terms":[]}}

Original: "фруктовое", Refinement: "без мяты"
{{"type":"EXCLUDE","queries":["тропический фрукт","персик","манго"],"excluded_terms":["мята","ментол"]}}

Original: "сладкое ягодное", Refinement: "нет, лучше тропическое и свежее"
{{"type":"PIVOT","queries":["тропический","маракуйя","ананас свежий"],"excluded_terms":[]}}

Original: "ягодное", Refinement: "именно клубника"
{{"type":"NARROW","queries":["клубника","клубника сливки","клубника лимон"],"excluded_terms":[]}}

Original: "Adalya что-нибудь", Refinement: "не Adalya, другой бренд"
{{"type":"EXCLUDE","queries":["BlackBurn","Duft","Must Have"],"excluded_terms":["Adalya"]}}\
"""

_REFINE_USER = 'Original: "{original}"\nRefinement: "{refinement}"\nJSON:'

# ── Альтернативы при пустом поиске ───────────────────────────────────────────

_SUGGEST_SYSTEM = """\
You are a tobacco search assistant. A user's search found NO results.
Suggest 2-3 alternative searches likely to find results. Output JSON only.

STRATEGY:
• Alt 1: closest synonym or broader term (same taste family)
• Alt 2: complementary flavor (different approach, same mood)
• Alt 3: popular substitute (what people usually search for instead)

DIVERSITY: alternatives must be clearly different from each other.
Use COMMON catalog terms — not rare or invented ones.

CATALOG FLAVORS (common ones): {flavors_sample}

EXAMPLES:
Failed: "Adalya Black Currant Limited Edition"
{{"reason":"слишком специфичная комбинация бренд+вкус+серия","alternatives":[{{"query":"Adalya смородина","hint":"тот же бренд, похожий вкус"}},{{"query":"чёрная смородина","hint":"искать у всех брендов"}},{{"query":"ягодный кислый","hint":"близкое направление"}}]}}

Failed: "кокосовый ром"
{{"reason":"составной вкус редко встречается в каталоге","alternatives":[{{"query":"кокос","hint":"один компонент"}},{{"query":"ром карамель","hint":"алкогольное + сладкое"}},{{"query":"тропический алкоголь","hint":"популярное направление"}}]}}

Failed: "клубнека"
{{"reason":"опечатка","alternatives":[{{"query":"клубника","hint":"исправленное написание"}},{{"query":"клубника сливки","hint":"популярное сочетание"}},{{"query":"лесные ягоды","hint":"шире, если клубники нет"}}]}}\
"""

_SUGGEST_USER = 'Failed query: "{query}"\nContext: {parsed}\nJSON:'

# ── Шум в mix-запросах ────────────────────────────────────────────────────────

_MIX_NOISE: frozenset[str] = frozenset({
    "и", "так", "вот", "ну", "а", "но", "хм", "ладно", "окей", "короче",
    "итак", "слушай", "слушайте", "знаешь", "знаете",
    "хочу", "хочется", "хотел", "хотела", "хотим", "хотелось", "хотелось бы", "хотел бы",
    "люблю", "любим", "люблем", "нравится", "нравятся", "нравилось", "обожаю", "обожаем",
    "покурить", "покурим", "закурить", "скурить", "подымить",
    "кальян", "кальяна", "кальяне", "кальянчик", "трубку",
    "микс", "миксовать", "смешать",
    "собери", "соберем", "соберём", "сделай", "составь",
    "подбери", "предложи", "придумай", "подскажи", "посоветуй",
    "приготовь", "скомпонуй", "скомбинируй",
    "я", "ты", "мы", "вы", "мне", "нам", "нас", "меня", "тебе",
    "с", "со", "ней", "ним", "него", "нею", "ею",
    "пожалуйста", "можно", "дай", "давай", "тогда",
})


def sanitize_user_input(text: str, max_len: int = 500) -> str:
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text or "")
    return cleaned.strip()[:max_len]


def extract_flavor_intent(description: str) -> str:
    cleaned = re.sub(r"[,\.!?;]", " ", description.lower())
    words = cleaned.split()
    filtered = [w for w in words if w not in _MIX_NOISE]
    result = " ".join(filtered).strip()
    return result if len(result) >= 2 else description.strip()


def _learned_key(query: str) -> str:
    return normalize_text(query)


def _load_learned() -> dict[str, str]:
    try:
        if _LEARNED_FILE.exists():
            return json.loads(_LEARNED_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("llm_learned load failed: %s", e)
    return {}


def _save_learned(mapping: dict[str, str]) -> None:
    try:
        _LEARNED_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LEARNED_FILE.write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("llm_learned save failed: %s", e)


_learned: dict[str, str] = _load_learned()


def save_learned_mapping(original: str, normalized: str) -> bool:
    key = _learned_key(original)
    if key in _learned:
        return False
    _learned[key] = normalized
    _save_learned(_learned)
    logger.info("LLM learned: %r → %r  (всего: %d)", original, normalized, len(_learned))
    return True


def get_learned_count() -> int:
    return len(_learned)


@lru_cache(maxsize=1)
def _vocab_lists() -> tuple[list[str], list[str]]:
    try:
        from oshisha.vocabulary import get_vocabulary

        v = get_vocabulary()
        brands = sorted({b.display for b in v.brands.values() if len(b.display) <= 25})
        flavors = sorted({f.display for f in v.flavors.values()})
        return brands, flavors
    except Exception as e:
        logger.warning("vocab load failed: %s", e)
        return [], []


def invalidate_vocab_cache() -> None:
    _vocab_lists.cache_clear()
    try:
        from oshisha.vocabulary import get_vocabulary

        get_vocabulary.cache_clear()  # type: ignore[attr-defined]
    except Exception:
        pass


def _score_tokens(text: str, tokens: set[str]) -> int:
    low = text.lower()
    return sum(1 for t in tokens if t and t in low)


def _relevant_vocab_for_query(
    query: str,
    *,
    max_brands: int = 25,
    max_flavors: int = 45,
) -> tuple[str, str]:
    brands, flavors = _vocab_lists()
    if not brands and not flavors:
        return "", ""

    tokens = set(normalize_text(query).split())
    if not tokens:
        b = brands[:max_brands]
        f = flavors[:max_flavors]
        return ", ".join(b), ", ".join(f)

    ranked_b = sorted(brands, key=lambda x: (-_score_tokens(x, tokens), x))
    ranked_f = sorted(flavors, key=lambda x: (-_score_tokens(x, tokens), x))
    pick_b = [x for x in ranked_b if _score_tokens(x, tokens) > 0][:max_brands]
    pick_f = [x for x in ranked_f if _score_tokens(x, tokens) > 0][:max_flavors]
    if len(pick_b) < 8:
        pick_b = (pick_b + ranked_b[: max_brands - len(pick_b)])[:max_brands]
    if len(pick_f) < 12:
        pick_f = (pick_f + ranked_f[: max_flavors - len(pick_f)])[:max_flavors]
    return ", ".join(pick_b), ", ".join(pick_f)


def _flavor_sample(max_items: int = 60, query: str = "") -> str:
    _, flavors = _vocab_lists()
    if query:
        _, rel = _relevant_vocab_for_query(query, max_brands=0, max_flavors=max_items)
        if rel:
            return rel
    return ", ".join(flavors[:max_items])


# ── HTTP / LLM вызовы ─────────────────────────────────────────────────────────

async def _ask(
    prompt: str,
    *,
    temperature: float = 0.3,
    json_mode: bool = False,
    system: str | None = None,
    max_tokens: int = 512,
) -> str:
    """Основной вызов LLM. system — опциональный system-промпт."""
    if _groq_key():
        return await _ask_groq(
            prompt, temperature=temperature, json_mode=json_mode,
            system=system, max_tokens=max_tokens,
        )
    return await _ask_ollama(prompt, temperature=temperature, system=system, max_tokens=max_tokens)


async def _retry(coro_factory, retries: int = 2):
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await coro_factory()
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
            last = exc
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
                if status == 429:
                    # Rate limit: ждём согласно Retry-After или фиксированный backoff
                    retry_after = exc.response.headers.get("retry-after")
                    wait = float(retry_after) if retry_after else 60.0 * (attempt + 1)
                    logger.warning("LLM rate limit 429, ждём %.0f сек (попытка %d)", wait, attempt + 1)
                    if attempt < retries:
                        await asyncio_sleep(wait)
                        continue
                elif status < 500:
                    raise
            if attempt < retries:
                await asyncio_sleep(0.4 * (attempt + 1))
    if last:
        raise last
    raise RuntimeError("retry exhausted")


async def asyncio_sleep(sec: float) -> None:
    import asyncio
    await asyncio.sleep(sec)


async def _ask_groq(
    prompt: str,
    *,
    temperature: float,
    json_mode: bool,
    system: str | None = None,
    max_tokens: int = 512,
) -> str:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    body: dict = {
        "model": _groq_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    async def _do():
        client = await _get_http_client()
        t0 = time.perf_counter()
        r = await client.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {_groq_key()}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        r.raise_for_status()
        ms = int((time.perf_counter() - t0) * 1000)
        _record_llm_call(ms)
        return r.json()["choices"][0]["message"]["content"].strip()

    try:
        return await _retry(_do)
    except Exception:
        _record_llm_call(0, error=True)
        raise


async def _ask_ollama(
    prompt: str,
    *,
    temperature: float,
    system: str | None = None,
    max_tokens: int = 512,
) -> str:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    async def _do():
        client = await _get_http_client()
        t0 = time.perf_counter()
        r = await client.post(
            f"{_ollama_url()}/api/chat",
            json={
                "model": _ollama_model(),
                "messages": messages,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        r.raise_for_status()
        ms = int((time.perf_counter() - t0) * 1000)
        _record_llm_call(ms)
        return r.json()["message"]["content"].strip()

    try:
        return await _retry(_do)
    except Exception:
        _record_llm_call(0, error=True)
        raise


# ── normalize_query ───────────────────────────────────────────────────────────

def _validate_normalize_result(query: str, result: str) -> str:
    if not result or not result.strip():
        return query
    r = result.strip().strip('"').strip("'")
    if len(r) > max(len(query) * 3, 120):
        return query
    return r


async def normalize_query(query: str) -> str:
    """Нормализует поисковый запрос через LLM. Возвращает строку."""
    query = sanitize_user_input(query)
    key = _learned_key(query)
    if key in _learned:
        cached = _learned[key]
        logger.info("LLM cache hit: %r → %r", query, cached)
        return cached

    try:
        brands_str, flavors_str = _relevant_vocab_for_query(query)
        system = _get_prompt("normalize_system", _NORMALIZE_SYSTEM).format(
            brands=brands_str[:1500],
            flavors=flavors_str[:2500],
        )
        user_msg = _NORMALIZE_USER.format(query=query)
        raw = await _ask(user_msg, temperature=0.0, json_mode=bool(_groq_key()), system=system, max_tokens=128)

        # Пробуем распарсить JSON и взять первый терм
        data = extract_json_object(raw)
        if data and isinstance(data.get("terms"), list) and data["terms"]:
            term = str(data["terms"][0]).strip()
            confidence = data.get("confidence", "medium")
            if term and len(term) <= 100:
                logger.info(
                    "normalize_query %r → %r (conf=%s, all=%s)",
                    query, term, confidence, data["terms"],
                )
                return _validate_normalize_result(query, term)

        # Fallback: raw строка
        return _validate_normalize_result(query, raw)

    except Exception as e:
        logger.warning("normalize_query failed: %s", e)
        return query


def _query_skips_llm_normalize(q: str) -> bool:
    from oshisha.query_parser import parse_query

    pre = parse_query(q)
    if pre.flavor_keys or pre.brand_key:
        return True
    return _learned_key(q) in _learned


def count_catalog_llm_slots(raw_queries: list[str]) -> int:
    """Сколько вызовов normalize_query понадобится (без кеша/словаря)."""
    n = 0
    for raw in raw_queries[:5]:
        q = sanitize_user_input(raw)
        if q and not _query_skips_llm_normalize(q):
            n += 1
    return n


async def queries_for_catalog(
    raw_queries: list[str],
    *,
    try_llm_slot: Callable[[], bool] | None = None,
) -> list[str]:
    """Нормализует запросы от LLM перед поиском в каталоге (параллельно)."""
    items: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for raw in raw_queries:
        q = sanitize_user_input(raw)
        if not q:
            continue
        key = normalize_text(q)
        if key in seen:
            continue
        seen.add(key)
        needs_llm = not _query_skips_llm_normalize(q)
        items.append((q, needs_llm))
        if len(items) >= 5:
            break

    sem = asyncio.Semaphore(_llm_max_parallel())

    async def _resolve(q: str, needs_llm: bool) -> str:
        if not needs_llm:
            return q
        if try_llm_slot is not None and not try_llm_slot():
            return q
        async with sem:
            return await normalize_query(q)

    if not items:
        return []

    norms = await asyncio.gather(*[_resolve(q, nl) for q, nl in items])
    out: list[str] = []
    seen_out: set[str] = set()
    for norm in norms:
        key = normalize_text(norm)
        if key in seen_out:
            continue
        seen_out.add(key)
        out.append(norm)
    return out


# ── recommend_or_clarify ──────────────────────────────────────────────────────

async def recommend_or_clarify(description: str) -> dict:
    """Решает: искать сразу или задать уточняющий вопрос.

    Возвращает:
      {"type": "search", "queries": [...]}
      {"type": "question", "question": "...", "options": [...]}
    """
    description = sanitize_user_input(description)
    try:
        sample = _flavor_sample(30, query=description)
        system = _get_prompt("clarify_system", _CLARIFY_SYSTEM).format(flavors_sample=sample)
        user_msg = _CLARIFY_USER.format(description=description)
        raw = await _ask(user_msg, temperature=0.2, json_mode=bool(_groq_key()), system=system, max_tokens=256)

        data = extract_json_object(raw)
        if data:
            if data.get("type") == "question" and data.get("question"):
                q = str(data["question"]).strip()
                options = [str(o) for o in data.get("options", []) if str(o).strip()]
                return {"type": "question", "question": q, "options": options}
            if data.get("type") == "search" and data.get("queries"):
                queries = [sanitize_user_input(str(q)) for q in data["queries"] if str(q).strip()]
                if queries:
                    return {"type": "search", "queries": queries[:5]}

        return {"type": "search", "queries": [description]}

    except Exception as e:
        logger.warning("recommend_or_clarify failed: %s", e)
        return {"type": "search", "queries": [description]}


async def recommend_queries(description: str) -> list[str]:
    decision = await recommend_or_clarify(description)
    if decision.get("type") == "question":
        return [description]
    return decision.get("queries") or [description]


# ── recommend_mix ─────────────────────────────────────────────────────────────

def _validate_mix(data: list) -> list[dict]:
    """Валидирует и очищает список рецептов миксов.

    Правила:
    - Рецепт должен иметь имя и минимум 2 компонента
    - Компоненты дедуплицируются внутри рецепта (case-insensitive)
    - Межрецептурная дедупликация: компонент уже встречавшийся в предыдущем рецепте
      не блокирует рецепт, но логируется — модель должна это избегать сама
    """
    valid = []
    seen_globally: set[str] = set()

    for m in data:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name", "")).strip()
        components = m.get("components", [])
        if not name or not isinstance(components, list):
            continue

        # Дедупликация внутри рецепта
        clean_comps: list[str] = []
        seen_in_recipe: set[str] = set()
        for c in components:
            c = str(c).strip()
            if c and c.lower() not in seen_in_recipe:
                clean_comps.append(c)
                seen_in_recipe.add(c.lower())

        if len(clean_comps) < 2:
            logger.debug("_validate_mix: skip %r — too few components after dedup", name)
            continue

        # Считаем сколько компонентов уже есть в других рецептах
        overlap = sum(1 for c in clean_comps if c.lower() in seen_globally)
        if overlap == len(clean_comps):
            # Полностью дублирующий рецепт — пропускаем
            logger.warning("_validate_mix: skip %r — all components already used in other recipes", name)
            continue

        for c in clean_comps:
            seen_globally.add(c.lower())

        valid.append({
            "name": name[:60],
            "components": clean_comps[:3],
            "mood": str(m.get("mood", "")).strip()[:30],
        })

    return valid[:3]


async def recommend_mix(description: str) -> list[dict]:
    """Генерирует 2-3 рецепта миксов. Возвращает [{"name", "components", "mood"}]."""
    try:
        flavor_intent = extract_flavor_intent(description)
        logger.info("recommend_mix: raw=%r → intent=%r", description, flavor_intent)
        user_msg = _MIX_USER.format(flavor_intent=flavor_intent)
        result = await _ask(
            user_msg,
            temperature=0.6,
            json_mode=bool(_groq_key()),  # json_mode работает: ответ = объект {"mixes":[...]}
            system=_get_prompt("mix_system", _MIX_SYSTEM),
            max_tokens=700,
        )
        # Сначала пробуем объект {"mixes": [...]}
        data = extract_json_object(result)
        if data and isinstance(data.get("mixes"), list):
            mixes_raw = data["mixes"]
        else:
            # Fallback: прямой массив (старый формат или ollama)
            mixes_raw = extract_json_array(result) or []

        if mixes_raw:
            valid = _validate_mix(mixes_raw)
            logger.info("recommend_mix: parsed %d valid recipes", len(valid))
            if valid:
                return valid
        logger.warning("recommend_mix: no valid JSON in response: %r", (result or "")[:200])
        return []
    except Exception as e:
        logger.warning("recommend_mix failed: %s", e)
        return []


# ── refine_queries ────────────────────────────────────────────────────────────

async def refine_queries(original: str, refinement: str) -> dict:
    """Уточняет поисковые запросы с учётом фидбека пользователя.

    Возвращает:
      {
        "type": "ADD|EXCLUDE|PIVOT|NARROW",
        "queries": [...],          # 2-4 поисковых терма
        "excluded_terms": [...]    # что исключить (пусто если нет)
      }
    """
    try:
        _, flavors_str = _relevant_vocab_for_query(f"{original} {refinement}")
        system = _get_prompt("refine_system", _REFINE_SYSTEM).format(flavors=flavors_str[:2000])
        user_msg = _REFINE_USER.format(original=original, refinement=refinement)
        raw = await _ask(user_msg, temperature=0.2, json_mode=bool(_groq_key()), system=system, max_tokens=256)

        data = extract_json_object(raw)
        if data and isinstance(data.get("queries"), list):
            queries = [sanitize_user_input(str(q)) for q in data["queries"] if str(q).strip()]
            excluded = [sanitize_user_input(str(e)) for e in data.get("excluded_terms", []) if str(e).strip()]
            rtype = str(data.get("type", "ADD")).upper()
            if queries:
                return {
                    "type": rtype,
                    "queries": queries[:5],
                    "excluded_terms": excluded,
                }

        # Fallback: CSV-парсинг на случай если модель вернула не JSON
        queries_fb = [sanitize_user_input(q) for q in raw.split(",") if q.strip()]
        return {
            "type": "ADD",
            "queries": queries_fb[:5] if queries_fb else [refinement],
            "excluded_terms": [],
        }

    except Exception as e:
        logger.warning("refine_queries failed: %s", e)
        return {"type": "ADD", "queries": [refinement], "excluded_terms": []}


# ── chat_about_hookah ─────────────────────────────────────────────────────────

_CHAT_SYSTEM = """\
You are a friendly hookah tobacco expert for a Russian online hookah shop.
Answer ONLY questions about: hookah tobacco (brands, flavors, blending, storage),
hookah equipment (bowls, coals, foil, hookahs), preparation technique, hookah culture.

OFF-TOPIC (anything unrelated to hookah/tobacco) → reply EXACTLY:
{"answer":"Я только про табаки и кальяны 🌿 Но могу помочь — подобрать вкус, найти позицию или проверить наличие.","action":"advise","action_query":null}

CATALOG ACTION — detect user intent and set "action":
• "advise"  — user wants a flavor recommendation or selection ("что посоветуешь", "какой подойдёт", "хочу попробовать что-нибудь")
• "search"  — user asks about a specific flavor/brand they might want to find ("есть ли", "что из X", "нашёл бы")
• "check"   — user wants to check availability of a specific named product
• null      — pure informational question (how-to, what-is, explanation, comparison)

"action_query": short Russian catalog search term if action is not null, else null.

STYLE: Russian, informal, friendly. 2-4 sentences. No markdown, no headers.

OUTPUT: JSON only, no prose.
{"answer":"...","action":"advise"|"search"|"check"|null,"action_query":"..."|null}
"""

_CHAT_USER = 'Question: "{text}"\nJSON:'


async def chat_about_hookah(text: str) -> dict:
    """Отвечает на вопрос о табаках/кальянах и предлагает действие бота.

    Возвращает:
      {
        "answer": "Текст ответа",
        "action": "advise" | "search" | "check" | None,
        "action_query": "поисковый запрос" | None,
      }
    """
    text = sanitize_user_input(text, max_len=300)
    _fallback = {
        "answer": "Не удалось ответить. Попробуй переформулировать вопрос.",
        "action": None,
        "action_query": None,
    }
    try:
        user_msg = _CHAT_USER.format(text=text)
        raw = await _ask(
            user_msg,
            temperature=0.5,
            json_mode=bool(_groq_key()),
            system=_get_prompt("chat_system", _CHAT_SYSTEM),
            max_tokens=350,
        )
        data = extract_json_object(raw)
        if not data or not isinstance(data.get("answer"), str):
            logger.warning("chat_about_hookah: no valid JSON, raw=%r", raw[:200])
            return _fallback

        answer = data["answer"].strip()[:800]
        raw_action = data.get("action")
        action = raw_action if raw_action in ("advise", "search", "check") else None
        raw_query = data.get("action_query")
        action_query = sanitize_user_input(str(raw_query)).strip()[:60] if raw_query else None

        logger.info(
            "chat_about_hookah: %r → action=%s query=%r",
            text[:60], action, action_query,
        )
        return {"answer": answer, "action": action, "action_query": action_query}

    except Exception as e:
        logger.warning("chat_about_hookah failed: %s", e)
        return _fallback


# ── suggest_alternatives ──────────────────────────────────────────────────────

async def suggest_alternatives(query: str, parsed_summary: str) -> dict:
    """Предлагает альтернативы при пустом поиске.

    Возвращает:
      {
        "reason": "...",
        "alternatives": [{"query": "...", "hint": "..."}, ...]
      }
    """
    try:
        sample = _flavor_sample(50, query=query)
        system = _get_prompt("suggest_system", _SUGGEST_SYSTEM).format(flavors_sample=sample)
        user_msg = _SUGGEST_USER.format(query=query, parsed=parsed_summary or query)
        raw = await _ask(user_msg, temperature=0.3, json_mode=bool(_groq_key()), system=system, max_tokens=300)

        data = extract_json_object(raw)
        if data and isinstance(data.get("alternatives"), list):
            alts = []
            for a in data["alternatives"]:
                if not isinstance(a, dict):
                    continue
                q = sanitize_user_input(str(a.get("query", ""))).strip()
                hint = str(a.get("hint", "")).strip()[:60]
                if q:
                    alts.append({"query": q, "hint": hint})
            if alts:
                return {
                    "reason": str(data.get("reason", "")).strip()[:120],
                    "alternatives": alts[:3],
                }

        # Fallback: вернуть сырой текст как одну альтернативу без hint
        clean = raw.strip()[:200]
        if clean:
            return {"reason": "", "alternatives": [{"query": clean, "hint": ""}]}
        return {"reason": "", "alternatives": []}

    except Exception as e:
        logger.warning("suggest_alternatives failed: %s", e)
        return {"reason": "", "alternatives": []}


# ── theme_to_queries ──────────────────────────────────────────────────────────

_THEME_SYSTEM = """\
You are a hookah tobacco expert. Decode any Russian theme, mood, occasion, or description into concrete catalog search terms.
Output JSON only. No prose.

{{"terms": ["...", ...], "theme_display": "..."}}

RULES:
• terms — 4-7 lowercase Russian flavor names (1-3 words each), real hookah catalog items
• theme_display — concise display name (1-3 words, capitalized Russian)
• NEVER use brand names; NEVER use abstract words (кислый, сладкий, свежий) — always concrete flavors
• For moods, seasons, occasions → infer flavors that match the feeling

CATALOG FLAVORS (prefer terms from this list): {flavors_sample}

EXAMPLES:
"выпечка" → {{"terms":["ваниль","корица","яблочный пирог","карамель","мёд"],"theme_display":"Выпечка"}}
"что-то для лета" → {{"terms":["арбуз","персик","маракуйя","дыня","лимонад"],"theme_display":"Летнее"}}
"уютный вечер" → {{"terms":["ваниль","карамель","шоколад","корица","сливки"],"theme_display":"Уютный вечер"}}
"резкое и горькое" → {{"terms":["тёмный шоколад","кофе","грейпфрут","кедровый орех","имбирь"],"theme_display":"Горькое"}}
"восточный базар" → {{"terms":["гранат","инжир","финик","анис","роза"],"theme_display":"Восточный базар"}}\
"""

_THEME_USER = 'Input: "{theme}"\nJSON:'


async def theme_to_queries(theme: str) -> dict:
    """Конвертирует тему в список поисковых запросов через LLM.

    Возвращает:
      {
        "terms": ["ваниль", "корица", ...],  # конкретные поисковые запросы
        "theme_display": "Выпечка"            # отображаемое название темы
      }
    """
    theme = sanitize_user_input(theme, max_len=80)
    _fallback = {"terms": [theme], "theme_display": theme.capitalize()}
    try:
        sample = _flavor_sample(50, query=theme)
        system = _get_prompt("theme_system", _THEME_SYSTEM).format(flavors_sample=sample)
        user_msg = _THEME_USER.format(theme=theme)
        raw = await _ask(
            user_msg,
            temperature=0.2,
            json_mode=bool(_groq_key()),
            system=system,
            max_tokens=256,
        )
        data = extract_json_object(raw)
        if data and isinstance(data.get("terms"), list):
            terms = [sanitize_user_input(str(t)).strip() for t in data["terms"] if str(t).strip()]
            display = str(data.get("theme_display", theme)).strip().capitalize()
            if terms:
                logger.info("theme_to_queries %r → %s (display=%r)", theme, terms, display)
                return {"terms": terms[:8], "theme_display": display}
        logger.warning("theme_to_queries: no valid JSON for %r: %r", theme, (raw or "")[:200])
        return _fallback
    except Exception as e:
        logger.warning("theme_to_queries failed: %s", e)
        return _fallback
