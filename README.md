# TobaccoBot

Telegram-бот для поиска табака и проверки списков через [oshisha.cc](https://oshisha.cc).

## Запуск локально

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # заполните токен и учётку Oshisha
python scripts/run_bot.py
```

Проверка доступа к Telegram API: `python scripts/check_telegram.py`.

В Telegram: `/start` — кнопки меню (поиск, одна позиция, список).
