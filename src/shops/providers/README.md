# Новый оптовый сайт

1. Скопируйте `stub.py` → `myshop.py`, реализуйте HTTP и парсинг каталога.
2. Зарегистрируйте в `__init__.py`:

```python
from .myshop import MyShopProvider
register_site("myshop", MyShopProvider)
```

3. Добавьте в `.env`: `SHOP_SITES=oshisha,myshop` (порядок: первый = primary для бота).
4. Словари вкусов — отдельный каталог `data/vocab/myshop/` (позже; сейчас oshisha использует `data/vocab/`).

Контракт: `shops.protocol.ShopProvider` — методы `check_list`, `search_flavor`, опционально корзина.

Сравнение: `ShopHub.compare_search_flavor()` / `compare_check_list()` или `python scripts/compare_sites.py`.
