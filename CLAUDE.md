# TBotTabak — правила проекта для Claude

Telegram-бот для поиска табака и работы с корзиной на [oshisha.cc](https://oshisha.cc).

---

## Быстрый старт

```bash
cd /root/Projects/TBotTabak

# Тесты
source .venv_linux/bin/activate
pytest

# Запуск (только для отладки — на этом сервере бот управляется systemd)
python scripts/run_bot.py
```

**В продакшне бот управляется через systemd:**
```bash
systemctl status tg-tabak-bot
journalctl -u tg-tabak-bot -f
systemctl restart tg-tabak-bot
```

> ⚠️ **Никогда не запускать `python scripts/run_bot.py` вручную на этом сервере** — возникнет конфликт `409 Conflict` с работающим сервисом. Только `systemctl`.

---

## Структура

```
src/
  bot/              # Telegram: команды, меню, форматирование
    handlers/       # Обработчики по сценариям (search, cart, check, advise…)
    app.py          # Сборка Application, регистрация хендлеров
    config.py       # BotConfig + load_config() — всё из .env
    service_async.py # asyncio.to_thread()-обёртки над синхронным OshishaService
  oshisha/          # Клиент oshisha.cc: авторизация, каталог, корзина, LLM
    service.py      # OshishaService — единая точка для скриптов/тестов
    catalog.py      # Поиск позиций, check_products
    catalog_cache.py # Локальный снимок каталога (TTL-кэш)
    auth.py         # Сессия httpx, login_email
    cart.py         # add_queries / add_checks / fetch_cart
    flavor_search.py # Нечёткий поиск по вкусам через snapshot
    llm.py          # Groq/Ollama — рейтлимит + тайм-аут
  shops/            # Реестр сайтов, ShopHub, сравнение между магазинами
    hub.py          # ShopHub: primary-сайт + compare_*
    protocol.py     # ShopProvider (Protocol)
    providers/      # Конкретные реализации (oshisha.py, stub.py)
data/
  sessions/         # Cookies httpx (oshisha.json) — не коммитить
  vocab/            # Словари брендов/вкусов; *.manual.json перекрывают generated/
  cart_log.jsonl    # Журнал добавлений в корзину
  cache/            # Снимки каталогов (catalog_snapshot_*.json)
scripts/            # CLI-утилиты (login, fetch_catalog, build_vocab…)
deploy/             # systemd unit-файл (tbottabak.service — шаблон)
```

---

## Архитектурные правила

### Async / sync граница
- `src/bot/` — **async** (python-telegram-bot).
- `src/oshisha/` и `src/shops/` — **sync** (httpx без async).
- Вызовы синхронного кода из async-хендлеров — **только через `service_async.py`** (`asyncio.to_thread`). Прямые синхронные вызовы в хендлерах блокируют event loop — нельзя.

### ShopHub vs OshishaService
- В хендлерах всегда используется **`ShopHub`** (через `app.bot_data[CONFIG_KEY]`).
- `OshishaService` — только в скриптах и тестах напрямую.
- Добавление нового магазина: скопировать `src/shops/providers/stub.py`, реализовать `ShopProvider`, зарегистрировать в `providers/__init__.py`.

### Словари (`data/vocab/`)
- `*.manual.json` — правки вручную, **перекрывают** `generated/`.
- При пересборке словарей (`build_vocab_from_catalog.py`) `manual.json` не трогать.
- Таксономия вкусов — `flavor_taxonomy.json`; пополняется через `/update_taxonomy` (только admin) или `scripts/update_taxonomy.py`.

### Конфигурация
- Всё через `.env` → `BotConfig` (dataclass, frozen).
- Новый параметр: добавить поле в `BotConfig`, прочитать в `load_config()`, задокументировать в `.env.example`.
- Секреты (`OSHISHA_PASSWORD`, токены) — только в `.env`, в git не попадают (`.gitignore`).

---

## Тесты

```bash
pytest                    # всё
pytest tests/test_query_parser.py  # конкретный файл
```

- `tests/conftest.py` — фикстуры, мок-сервис.
- HTTP к внешним сайтам в тестах — **не делать**; использовать snapshot-фикстуры.
- Новые фичи — покрывать тестом в `tests/`.

---

## Переменные окружения (ключевые)

| Переменная | Обязательная | Назначение |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ | Токен бота (`8539432656:...`) |
| `OSHISHA_EMAIL` | ✅* | Логин на oshisha.cc |
| `OSHISHA_PASSWORD` | ✅* | Пароль на oshisha.cc |
| `TELEGRAM_ADMIN_IDS` | нет | ID админов (журнал корзины, `/update_taxonomy`) |
| `GROQ_API_KEY` | нет | LLM через Groq; если пусто — Ollama |
| `SHOP_SITES` | нет | `oshisha` (default); через запятую для multi |
| `CATALOG_WARMUP` | нет | `1` = прогрев снимка при старте |
| `TELEGRAM_PROXY` | нет | Прокси до api.telegram.org (для РФ) |

*Нужны для первичного логина; после этого сессия в `data/sessions/oshisha.json`.

---

## Деплой на этом сервере

- Сервис: `tg-tabak-bot.service` (`/etc/systemd/system/tg-tabak-bot.service`)
- Рабочая директория: `/root/Projects/TBotTabak`
- Venv: `.venv_linux/`
- Логи: `journalctl -u tg-tabak-bot -f`

После изменений кода:
```bash
systemctl restart tg-tabak-bot
journalctl -u tg-tabak-bot -f  # проверить старт
```

После изменений `.env`:
```bash
systemctl daemon-reload && systemctl restart tg-tabak-bot
```

---

## Частые ловушки

| Ситуация | Что делать |
|---|---|
| `409 Conflict` в логах | Найти дублирующий процесс: `ps aux \| grep run_bot` → `kill -9 <PID>` |
| `OshishaAuthError: Нет сессии` | `python scripts/login_oshisha.py` (один раз вручную) |
| Снимок каталога устарел | `systemctl restart tg-tabak-bot` (CATALOG_WARMUP=1) или `/update_taxonomy` в боте |
| Тесты падают на импорте | `source .venv_linux/bin/activate`, затем `pytest` |
