"""Ночной LLM-анализ пользовательских запросов.

Каждую ночь (по умолчанию в 03:00) читает логи за прошедший день,
отправляет на анализ LLM и высылает дайджест администраторам в Telegram.

Переменные окружения:
    NIGHT_DIGEST_HOUR   — час запуска (0–23), по умолчанию 3
    NIGHT_DIGEST_MIN_Q  — минимум Q-записей для запуска анализа, по умолчанию 3
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import Application

from bot.config import BotConfig
from bot.handlers.common import CONFIG_KEY
from bot.search_log import get_zero_result_queries
from bot.vocab_patch import generate_vocab_patch, save_patch, format_patch_preview
from oshisha.llm import _ask  # noqa: WPS450 — внутренняя функция, но здесь нужна

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = ROOT / "data" / "user_logs"
REPORTS_DIR = ROOT / "data" / "reports"
TAXONOMY_PATH = ROOT / "data" / "vocab" / "flavor_taxonomy.json"

VOCAB_ZERO_RESULT_DAYS = 14   # глубина анализа провальных запросов (дней)

MAX_PAIRS_IN_PROMPT = 100   # ограничение на число Q/A пар чтобы не раздувать промпт
MAX_PREVIEW_IN_PROMPT = 250 # символов preview ответа в промпте


# ── Системный промпт ──────────────────────────────────────────────────────────

_DIGEST_SYSTEM = """\
Ты аналитик Telegram-бота для поиска кальянного табака. \
Бот помогает пользователям найти табак по вкусу, получить рекомендации, построить миксы.

Типы запросов (intent):
  flavor_search — поиск по конкретному вкусу
  advise        — советник (ИИ подбирает варианты по описанию)
  mix           — подбор рецептов миксов
  theme         — тематический поиск (выпечка, тропики и т.д.)
  chat          — свободный разговор
  check         — проверка наличия конкретных позиций
  ack           — короткое подтверждение (спасибо, ок)
  refine        — уточнение предыдущего результата

Проанализируй диалоги за день и напиши краткий отчёт на русском для владельца бота.

Формат ответа — Telegram HTML (теги <b>, <i>, <code>, эмодзи). До 700 слов.

Обязательные разделы:

<b>🔴 Что не сработало</b>
Запросы с ошибками, нулевыми результатами или явным промахом. \
Что, вероятно, хотел пользователь и почему не нашлось.

<b>💡 Непонятые намерения</b>
Запросы где бот, похоже, неправильно интерпретировал желание \
(например, отправил в chat вместо поиска, или ответил невпопад).

<b>🔁 Повторные попытки</b>
Пользователи, которые искали похожее несколько раз подряд — \
что не устроило с первого раза.

<b>🆕 Новые термины и пробелы</b>
Слова и фразы из запросов, которые не являются стандартными вкусами/брендами — \
кандидаты для добавления в словарь.

<b>📊 Итог дня</b>
3–5 коротких bullet-пунктов: что работало хорошо, что стоит улучшить.

Если данных мало (< 5 запросов) — напиши просто краткую заметку без разделов.
Не выдумывай детали сверх предоставленных диалогов. Будь конкретным.\
"""


# ── Загрузка логов ────────────────────────────────────────────────────────────

def _load_day_entries(day: str) -> list[dict]:
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


def _build_sessions(entries: list[dict]) -> dict[str, list[dict]]:
    """Группировка Q/A пар по пользователю (username или user_id)."""
    sessions: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        uid = e.get("username") or str(e.get("user_id", "unknown"))
        sessions[uid].append(e)
    return dict(sessions)


# ── Формирование промпта ──────────────────────────────────────────────────────

def _format_sessions_for_prompt(entries: list[dict], day: str) -> str:
    """Превращает записи дня в компактный текст для LLM."""
    sessions = _build_sessions(entries)
    q_entries = [e for e in entries if e.get("type") == "Q"]
    total_q = len(q_entries)
    total_users = len(sessions)

    lines: list[str] = [
        f"== {day}, пользователей: {total_users}, запросов: {total_q} ==\n",
    ]

    pairs_added = 0
    for username, user_entries in sessions.items():
        q_count = sum(1 for e in user_entries if e.get("type") == "Q")
        lines.append(f"@{username} ({q_count} запр.):")

        i = 0
        while i < len(user_entries) and pairs_added < MAX_PAIRS_IN_PROMPT:
            e = user_entries[i]
            if e.get("type") == "Q":
                intent = e.get("intent", "?")
                text = e.get("text", "")[:200]
                line = f'  Q[{intent}]: "{text}"'

                # Ищем следующую A для этого же юзера
                answer = None
                for j in range(i + 1, min(i + 4, len(user_entries))):
                    if user_entries[j].get("type") == "A":
                        answer = user_entries[j]
                        break

                if answer:
                    preview = (answer.get("preview") or "")[:MAX_PREVIEW_IN_PROMPT]
                    # Заменяем переносы на пробел чтобы не раздувать промпт
                    preview_flat = preview.replace("\n", " · ").strip()
                    line += f" → {preview_flat}"
                else:
                    line += " → (ответ не записан)"

                lines.append(line)
                pairs_added += 1
            i += 1

        lines.append("")

    if pairs_added >= MAX_PAIRS_IN_PROMPT:
        lines.append(f"[Показано {pairs_added} из {total_q} запросов]")

    return "\n".join(lines)


# ── Вызов LLM ─────────────────────────────────────────────────────────────────

async def _call_digest_llm(prompt_body: str) -> str:
    """Вызвать LLM для анализа. Возвращает текст дайджеста."""
    return await _ask(
        prompt_body,
        system=_DIGEST_SYSTEM,
        temperature=0.4,
        max_tokens=1800,
        json_mode=False,
    )


# ── Отправка администраторам ──────────────────────────────────────────────────

async def _send_to_admins(bot: Bot, config: BotConfig, text: str) -> None:
    """Отправить текст всем администраторам. Разбивает длинные сообщения."""
    if not config.telegram_admin_ids:
        logger.warning("night_digest: TELEGRAM_ADMIN_IDS не задан, некому отправить")
        return

    # Telegram максимум 4096 символов на сообщение
    chunks: list[str] = []
    while len(text) > 4000:
        split_at = text.rfind("\n", 0, 4000)
        if split_at < 0:
            split_at = 4000
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    chunks.append(text)

    for admin_id in config.telegram_admin_ids:
        for i, chunk in enumerate(chunks):
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=chunk,
                    parse_mode=ParseMode.HTML,
                )
            except TelegramError as exc:
                logger.warning(
                    "night_digest: не удалось отправить часть %d/%d admin %d: %s",
                    i + 1, len(chunks), admin_id, exc,
                )


# ── Анализ словаря и таксономии ───────────────────────────────────────────────

_VOCAB_SYSTEM = """\
Ты эксперт по кальянному табаку и помогаешь улучшить словарь поиска бота.

Словарь — это список ключевых слов/фраз. Когда пользователь пишет слово из словаря,
бот находит подходящие табаки по этому ключу. Когда слова нет — бот ничего не находит.

Каждый ключ словаря — это «профиль»: к нему привязаны поисковые термины из каталога.
Например: ключ «кислый» → ищет в каталоге «грейпфрут», «лимон», «кислых ягод» и т.д.

Твоя задача: проанализировать провальные запросы пользователей и предложить улучшения.
Будь конкретным — не «добавить сладкое», а «добавить ключ "лето в стакане" → синоним к \
теме "освежающее летнее"».\
"""


def _load_taxonomy_summary() -> str:
    """Загружает таксономию и возвращает компактное текстовое резюме для LLM.

    Формат: секция → список ключей через запятую.
    Поисковые термины не включаются — только сами ключи словаря.
    """
    if not TAXONOMY_PATH.exists():
        return "(таксономия не найдена)"
    try:
        data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        profiles = data.get("profiles", data)
    except Exception as exc:
        return f"(ошибка чтения таксономии: {exc})"

    lines: list[str] = []
    current_section = "Без секции"
    section_keys: list[str] = []

    def _flush():
        if section_keys:
            lines.append(f"{current_section}: {', '.join(section_keys)}")

    for key, val in profiles.items():
        if key.startswith("──"):
            _flush()
            section_keys = []
            # Извлекаем название секции из разделителя
            current_section = key.strip("─ ").strip()
        else:
            section_keys.append(key)

    _flush()
    return "\n".join(lines)


def _extract_unknown_terms(entries: list[dict], taxonomy_keys: set[str]) -> list[str]:
    """Возвращает слова из запросов пользователей, которых нет в таксономии.

    Работает только с flavor_search запросами — именно они ищут по словарю.
    """
    unknown: dict[str, int] = {}
    for e in entries:
        if e.get("type") != "Q" or e.get("intent") != "flavor_search":
            continue
        text = e.get("text", "").lower()
        for word in text.split():
            word = word.strip(".,!?\"'()").strip()
            if len(word) >= 3 and word not in taxonomy_keys:
                unknown[word] = unknown.get(word, 0) + 1

    # Возвращаем топ по частоте, исключаем числа и граммовки
    filtered = [
        (w, cnt) for w, cnt in unknown.items()
        if not w.isdigit() and not w.endswith("г") and not w.endswith("гр")
    ]
    filtered.sort(key=lambda x: x[1], reverse=True)
    return [f"{w} (×{cnt})" if cnt > 1 else w for w, cnt in filtered[:30]]


def _build_vocab_prompt(
    zero_results: list[tuple[str, int]],
    taxonomy_summary: str,
    unknown_terms: list[str],
) -> str:
    lines: list[str] = [
        f"Текущий словарь (по секциям):\n{taxonomy_summary}\n",
    ]

    if zero_results:
        lines.append(f"Запросы без результатов за последние {VOCAB_ZERO_RESULT_DAYS} дней:")
        for query, cnt in zero_results[:30]:
            lines.append(f"  {cnt}x  \"{query}\"")
    else:
        lines.append("Запросов без результатов не обнаружено.")

    if unknown_terms:
        lines.append(f"\nСлова из запросов, которых нет в словаре:")
        lines.append("  " + ", ".join(unknown_terms))

    lines.append(
        "\nДля каждого провального запроса ответь:\n"
        "1. Что хотел пользователь?\n"
        "2. Есть ли это в словаре под другим словом? (если да — предложи синоним)\n"
        "3. Если в словаре нет — конкретно что добавить: новый ключ и примерные термины "
        "поиска из каталога (вкусы, не бренды)\n\n"
        "Формат ответа — Telegram HTML (<b>, <i>, •). До 500 слов. Будь конкретен."
    )

    return "\n".join(lines)


async def run_vocab_analysis(entries_today: list[dict]) -> str | None:
    """Анализ пробелов в словаре таксономии. Возвращает текст или None при ошибке."""
    zero_results = get_zero_result_queries(min_occurrences=1)
    if not zero_results and not entries_today:
        logger.info("vocab_analysis: нет данных для анализа")
        return None

    taxonomy_summary = _load_taxonomy_summary()

    # Ключи таксономии для поиска незнакомых слов
    try:
        data = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
        profiles = data.get("profiles", data)
        taxonomy_keys = {k.lower() for k in profiles if not k.startswith("──")}
    except Exception:
        taxonomy_keys = set()

    unknown_terms = _extract_unknown_terms(entries_today, taxonomy_keys)

    prompt = _build_vocab_prompt(zero_results, taxonomy_summary, unknown_terms)

    logger.info(
        "vocab_analysis: %d провальных запросов, %d незнакомых слов",
        len(zero_results), len(unknown_terms),
    )

    return await _ask(
        prompt,
        system=_VOCAB_SYSTEM,
        temperature=0.3,
        max_tokens=1500,
        json_mode=False,
    )


# ── Сохранение отчёта ─────────────────────────────────────────────────────────

def _save_report(day: str, text: str) -> None:
    """Сохранить отчёт в data/reports/YYYY-MM-DD.md."""
    try:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORTS_DIR / f"{day}.md"
        path.write_text(
            f"# Дайджест {day}\n\n{text}\n",
            encoding="utf-8",
        )
        logger.info("night_digest: отчёт сохранён в %s", path)
    except Exception as exc:
        logger.warning("night_digest: не удалось сохранить отчёт: %s", exc)


# ── Главная функция дайджеста ─────────────────────────────────────────────────

async def run_night_digest(
    application: Application,
    *,
    target_date: str | None = None,
) -> str | None:
    """Запустить анализ за указанный день (по умолчанию — вчера).

    Возвращает текст дайджеста или None если данных нет.
    """
    if target_date is None:
        target_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    config: BotConfig = application.bot_data.get(CONFIG_KEY)
    if config is None:
        logger.warning("night_digest: конфиг не найден в bot_data")
        return None

    min_q = int(os.environ.get("NIGHT_DIGEST_MIN_Q", "3"))

    entries = _load_day_entries(target_date)
    q_count = sum(1 for e in entries if e.get("type") == "Q")

    if q_count < min_q:
        logger.info(
            "night_digest: пропускаем %s — только %d запросов (порог %d)",
            target_date, q_count, min_q,
        )
        return None

    logger.info("night_digest: анализируем %s, %d запросов", target_date, q_count)

    # ── Анализ 1: запросы пользователей ──────────────────────────────────────
    prompt_body = _format_sessions_for_prompt(entries, target_date)
    try:
        digest_text = await _call_digest_llm(prompt_body)
    except Exception as exc:
        logger.error("night_digest: LLM (запросы) вернул ошибку: %s", exc)
        digest_text = f"Не удалось получить анализ от LLM: <code>{exc}</code>"

    header = f"📋 <b>Дайджест за {target_date}</b>  ({q_count} запросов)\n\n"
    full_digest = header + digest_text

    # ── Анализ 2: пробелы в словаре ──────────────────────────────────────────
    # Пауза между вызовами Groq чтобы не выбить rate limit (tokens/min)
    await asyncio.sleep(35)

    vocab_text: str | None = None
    try:
        vocab_text = await run_vocab_analysis(entries)
    except Exception as exc:
        logger.error("night_digest: LLM (словарь) вернул ошибку: %s", exc)
        vocab_text = f"Не удалось проанализировать словарь: <code>{exc}</code>"

    vocab_full: str | None = None
    if vocab_text:
        vocab_full = f"📚 <b>Анализ словаря</b>\n\n{vocab_text}"

    # ── Патч 2Г: генерация JSON-патча для taxonomy ────────────────────────────
    await asyncio.sleep(35)

    zero_results = get_zero_result_queries(min_occurrences=1)
    try:
        taxonomy_keys = set(_load_taxonomy_summary().replace(": ", "\n").split())
        unknown_terms = _extract_unknown_terms(entries, taxonomy_keys)
        patch = await generate_vocab_patch(entries, zero_results, unknown_terms)
    except Exception as exc:
        logger.error("night_digest: ошибка генерации патча: %s", exc)
        patch = None

    patch_preview: str | None = None
    if patch:
        try:
            patch_path = save_patch(patch, target_date)
            patch_preview = format_patch_preview(patch)
            logger.info("vocab_patch сохранён: %s", patch_path)
        except Exception as exc:
            logger.warning("vocab_patch: не удалось сохранить: %s", exc)

    # ── Сохранение и отправка ─────────────────────────────────────────────────
    report_parts = [full_digest]
    if vocab_full:
        report_parts.append(vocab_full)
    if patch_preview:
        report_parts.append(patch_preview)
    _save_report(target_date, "\n\n---\n\n".join(report_parts))

    await _send_to_admins(application.bot, config, full_digest)
    if vocab_full:
        await _send_to_admins(application.bot, config, vocab_full)
    if patch_preview:
        await _send_to_admins(application.bot, config, patch_preview)

    return full_digest


# ── Фоновый цикл ─────────────────────────────────────────────────────────────

def _digest_hour() -> int:
    return max(0, min(23, int(os.environ.get("NIGHT_DIGEST_HOUR", "3"))))


async def night_digest_loop(application: Application) -> None:
    """Ждёт следующего NIGHT_DIGEST_HOUR и каждый день запускает дайджест."""
    hour = _digest_hour()
    logger.info("night_digest: ежедневный запуск в %02d:00", hour)

    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        wait_sec = (target - now).total_seconds()
        logger.debug(
            "night_digest: следующий запуск через %.0f мин (%s)",
            wait_sec / 60, target.strftime("%Y-%m-%d %H:%M"),
        )

        try:
            await asyncio.sleep(wait_sec)
        except asyncio.CancelledError:
            logger.info("night_digest_loop: отменён, завершаем")
            raise

        try:
            await run_night_digest(application)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("night_digest: неожиданная ошибка, продолжаем")


def start_night_digest(application: Application) -> None:
    """Запустить фоновый цикл дайджеста (вызывается из post_init)."""
    asyncio.create_task(
        night_digest_loop(application),
        name="night_digest",
    )
    logger.info("night_digest: фоновая задача запущена")
