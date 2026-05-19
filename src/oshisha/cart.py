"""Добавление товаров в корзину oshisha.cc."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

from .auth import OshishaAuth, OshishaAuthError
from .catalog import OshishaCatalog, ProductCheckResult

ADD2BASKET_PATH = "/local/templates/Oshisha/include/add2basket.php"
BASKET_AJAX_PATH = "/bitrix/components/bitrix/sale.basket.basket/ajax.php"
CART_URL = "/personal/cart/"
DEFAULT_SITE_ID = "N2"


@dataclass
class CartAddResult:
    """Результат добавления одной позиции."""

    query: str
    success: bool
    message: str
    matched_name: str | None = None
    product_id: str | None = None
    quantity: int = 0
    line_price: int | None = None


@dataclass
class CartLineItem:
    """Строка корзины на сайте."""

    name: str
    quantity: float
    price: float | None = None
    sum_price: float | None = None
    product_id: str | None = None


@dataclass
class CartView:
    """Содержимое корзины Oshisha."""

    items: list[CartLineItem] = field(default_factory=list)
    total_sum: float | None = None
    total_sum_formatted: str | None = None
    cart_url: str = CART_URL
    empty: bool = False


@dataclass
class CartAddBatchResult:
    """Итог пакетного добавления."""

    items: list[CartAddResult] = field(default_factory=list)
    cart_quantity: int | None = None
    cart_sum_price: int | None = None
    cart_url: str = CART_URL

    @property
    def added_count(self) -> int:
        return sum(1 for i in self.items if i.success)


class OshishaCart:
    """Корзина через add2basket.php (как кнопка «В корзину» на сайте)."""

    def __init__(self, auth: OshishaAuth, *, site_id: str = DEFAULT_SITE_ID) -> None:
        self.auth = auth
        self.base_url = auth.base_url
        self.site_id = site_id

    def _post_items(self, payloads: list[dict[str, Any]]) -> dict[str, Any]:
        if not payloads:
            raise ValueError("Пустой список для корзины")
        url = urljoin(self.base_url, ADD2BASKET_PATH)
        body = "product_data=" + json.dumps(payloads, ensure_ascii=False)
        resp = self.auth._client.post(
            url,
            content=body,
            headers={
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Origin": self.base_url,
                "Referer": f"{self.base_url}/catalog/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise OshishaAuthError(f"Некорректный ответ корзины: {resp.text[:200]}") from exc
        if data.get("STATUS") != "success":
            raise OshishaAuthError(f"Корзина: {data}")
        return data

    @staticmethod
    def _payload_from_check(check: ProductCheckResult) -> dict[str, Any] | None:
        if check.status == "не найден" or not check.product_id:
            return None
        if check.status == "нет":
            return None
        qty = max(1, check.pack_count)
        unit = int(check.price) if check.price is not None else 0
        return {
            "ID": str(check.product_id),
            "QUANTITY": qty,
            "TYPE": "add",
            "PRICE": unit * qty,
            "BRAND": "",
        }

    def add_checks(
        self,
        checks: list[ProductCheckResult],
        *,
        queries: list[str] | None = None,
    ) -> CartAddBatchResult:
        """Добавить уже найденные позиции одним запросом к API."""
        if queries is None:
            queries = [c.query for c in checks]

        results: list[CartAddResult] = []
        payloads: list[dict[str, Any]] = []

        for check, query in zip(checks, queries, strict=True):
            if check.status == "не найден":
                results.append(
                    CartAddResult(query=query, success=False, message="не найден")
                )
                continue
            if check.status == "нет":
                results.append(
                    CartAddResult(
                        query=query,
                        success=False,
                        message="нет в наличии",
                        matched_name=check.matched_name,
                        product_id=check.product_id,
                    )
                )
                continue
            payload = self._payload_from_check(check)
            if not payload:
                results.append(
                    CartAddResult(
                        query=query,
                        success=False,
                        message="не удалось добавить",
                        matched_name=check.matched_name,
                    )
                )
                continue
            payloads.append(payload)
            results.append(
                CartAddResult(
                    query=query,
                    success=False,
                    message="_pending",
                    matched_name=check.matched_name,
                    product_id=check.product_id,
                    quantity=payload["QUANTITY"],
                    line_price=payload["PRICE"],
                )
            )

        batch = CartAddBatchResult(items=results)
        if not payloads:
            return batch

        api = self._post_items(payloads)
        api_items = api.get("ITEMS") or {}
        batch.cart_quantity = _to_int(api.get("QUANTITY"))
        batch.cart_sum_price = _to_int(api.get("SUM_PRICE"))
        batch.cart_url = urljoin(self.base_url, CART_URL)

        payload_idx = 0
        for item in batch.items:
            if item.message != "_pending":
                continue
            pid = payloads[payload_idx]["ID"]
            payload_idx += 1
            row = api_items.get(pid) or api_items.get(str(pid))
            item.success = True
            item.message = "добавлено"
            if row:
                item.matched_name = row.get("NAME") or item.matched_name
                item.line_price = _to_int(row.get("PRICE")) or item.line_price

        return batch

    def fetch_cart(self) -> CartView:
        """Текущая корзина аккаунта (Bitrix sale.basket.basket/ajax.php)."""
        sessid = self.auth.fetch_sessid()
        url = urljoin(self.base_url, BASKET_AJAX_PATH)
        resp = self.auth._client.post(
            url,
            data={
                "sessid": sessid,
                "site_id": self.site_id,
                "basketAction": "recalculateAjax",
            },
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.base_url,
                "Referer": urljoin(self.base_url, CART_URL),
            },
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise OshishaAuthError(f"Некорректный ответ корзины: {resp.text[:200]}") from exc

        basket_data = data.get("BASKET_DATA") or {}
        if basket_data.get("EMPTY_BASKET"):
            return CartView(
                empty=True,
                cart_url=urljoin(self.base_url, CART_URL),
            )

        rows = (basket_data.get("GRID") or {}).get("ROWS") or {}
        items: list[CartLineItem] = []
        for row in rows.values():
            if not isinstance(row, dict):
                continue
            name = str(row.get("NAME") or "").strip()
            if not name:
                continue
            qty = _to_float(row.get("QUANTITY")) or 0.0
            price = _to_float(row.get("PRICE"))
            sum_price = _to_float(row.get("SUM"))
            if sum_price is None and price is not None:
                sum_price = price * qty
            items.append(
                CartLineItem(
                    name=name,
                    quantity=qty,
                    price=price,
                    sum_price=sum_price,
                    product_id=str(row.get("PRODUCT_ID") or "") or None,
                )
            )

        total = _to_float(basket_data.get("allSum"))
        return CartView(
            items=items,
            total_sum=total,
            total_sum_formatted=basket_data.get("allSum_FORMATED"),
            cart_url=urljoin(self.base_url, CART_URL),
            empty=not items,
        )

    def add_queries(self, catalog: OshishaCatalog, lines: list[str]) -> CartAddBatchResult:
        """Найти позиции и добавить в корзину."""
        checks: list[ProductCheckResult] = []
        queries: list[str] = []
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            queries.append(text)
            checks.append(catalog.check_product(text))
        return self.add_checks(checks, queries=queries)


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
