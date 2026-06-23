# TobaccoBot

Telegram-бот для поиска табака, проверки списков и корзины на [oshisha.cc](https://oshisha.cc).

## Структура

| Путь | Назначение |
|------|------------|
| `src/bot/` | Telegram: команды, меню, форматирование |
| `src/bot/handlers/` | Обработчики по сценариям (поиск, советник, корзина, …) |
| `src/oshisha/` | Клиент oshisha.cc: авторизация, каталог, корзина, LLM |
| `src/shops/` | Реестр сайтов, `ShopHub`, сравнение между магазинами |
| `data/vocab/` | Словари брендов и вкусов (`*.manual.json` перекрывают generated) |
| `scripts/` | CLI: логин, каталог, запуск бота, обновление таксономии |

HTTP-запросы к Oshisha выполняются в thread pool (`bot/service_async.py`), чтобы не блокировать async-бот.

## Запуск локально

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux:   source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # TELEGRAM_BOT_TOKEN, OSHISHA_*, при необходимости GROQ_API_KEY
python scripts/login_oshisha.py   # один раз: сессия в data/sessions/
python scripts/run_bot.py
```

Проверка Telegram API: `python scripts/check_telegram.py`.

В боте: `/start` — меню, `/help` — справка. Советник и нормализация запросов используют LLM (Groq, если задан `GROQ_API_KEY`, иначе Ollama).

## Несколько сайтов и сравнение

В `.env` задаётся `SHOP_SITES=oshisha` (через запятую). Первый сайт — основной для бота (поиск, корзина).

Добавление нового сайта — только код: см. `src/shops/providers/README.md` (скопировать `stub.py`, `register_site()`).

```bash
# Сравнить поиск на всех активных сайтах
python scripts/compare_sites.py search "малина 200"

# Сравнить список из файла
python scripts/compare_sites.py list data/sample_list.txt
```

В коде: `ShopHub.from_env().compare_search_flavor(query)` / `compare_check_list(lines)`.

## Тесты

```bash
pip install -r requirements-dev.txt
pytest
```

## Словари и таксономия

```bash
python scripts/fetch_catalog.py
python scripts/build_vocab_from_catalog.py
python scripts/update_taxonomy.py   # админы: также /update_taxonomy в боте
```

## Деплой (systemd на VPS)

```bash
sudo useradd -r -m -d /opt/tbottabak tbottabak   # один раз
git clone … /opt/tbottabak && cd /opt/tbottabak
sudo -u tbottabak python3 -m venv .venv
sudo -u tbottabak .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
bash deploy/install-systemd.sh
```

Unit-файл подставляет путь репозитория и запускает бота от пользователя `tbottabak` (не root).

Логи: `journalctl -u tbottabak -f`
