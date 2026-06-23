# Технический долг

## TD-001: Проверка прав в групповом чате (`cart_flow.py`)

**Файл:** `src/bot/handlers/cart_flow.py` — функция `_run_cart_log_from_chat`

**Проблема:**
```python
# ~строка 265
show_all = can_view_all_cart_log(config, chat_id)  # ← BUG: chat_id, не user_id
```

В групповом чате `chat_id` — это ID группы, а не пользователя. Проверка
`TELEGRAM_ADMIN_IDS` не сработает: кнопка «📜 Журнал» не покажет полный лог
администратору бота в групповом чате.

**Текущий workaround:** бот используется в личных чатах — баг не проявляется.

**Правка:** заменить `chat_id` на реальный `user_id` из `callback_query.from_user.id`.  
**Сложность:** 🟢 Малая (2–3 строки)
