"""Встроенные провайдеры — импорт регистрирует сайты в реестре."""

from shops.registry import register_site

from .oshisha import OshishaShopProvider

register_site("oshisha", OshishaShopProvider)

# Раскомментируйте для локальной отладки сравнения без второго реального сайта:
# from .stub import StubShopProvider
# register_site("stub", lambda: StubShopProvider(site_id="stub"))

__all__ = ["OshishaShopProvider"]
