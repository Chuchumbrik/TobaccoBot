"""Тесты для OshishaCart: add_checks, add_from_products, fetch_cart."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from oshisha.auth import OshishaAuthError
from oshisha.cart import (
    OshishaCart,
    CartAddResult,
)
from oshisha.catalog import CatalogProduct, ProductCheckResult


# ── Вспомогательные фабрики ───────────────────────────────────────────────────

def _product(
    pid: str = "1",
    name: str = "BlackBurn Малина 200г",
    can_buy: bool = True,
    price: float = 1200.0,
    max_quantity: float | None = None,
) -> CatalogProduct:
    return CatalogProduct(
        id=pid,
        name=name,
        url=f"/catalog/item-{pid}/",
        can_buy=can_buy,
        max_quantity=max_quantity,
        price=price,
        base_price=price,
        currency="RUB",
        raw={},
    )


def _check(
    query: str = "bb малина 200",
    status: str = "есть",
    pid: str = "1",
    price: float = 1200.0,
    matched_name: str = "BlackBurn Малина 200г",
    pack_count: int = 1,
) -> ProductCheckResult:
    return ProductCheckResult(
        query=query,
        status=status,
        matched_name=matched_name if status != "не найден" else None,
        product_id=pid if status != "не найден" else None,
        price=price if status != "не найден" else None,
        pack_count=pack_count,
    )


def _mock_auth(api_response: dict | None = None) -> MagicMock:
    """Mock OshishaAuth с настроенным ответом HTTP."""
    auth = MagicMock()
    auth.base_url = "https://oshisha.cc"
    auth.fetch_sessid.return_value = "test_sessid_abc"
    if api_response is not None:
        resp = MagicMock()
        resp.json.return_value = api_response
        resp.text = json.dumps(api_response)
        resp.raise_for_status.return_value = None
        auth._client.post.return_value = resp
    return auth


def _add2basket_success(product_ids: list[str], price: int = 1200) -> dict:
    """Типичный успешный ответ add2basket API."""
    items = {pid: {"NAME": f"Product {pid}", "PRICE": price} for pid in product_ids}
    return {
        "STATUS": "success",
        "ITEMS": items,
        "QUANTITY": len(product_ids),
        "SUM_PRICE": price * len(product_ids),
    }


# ── OshishaCart._payload_from_product (static) ───────────────────────────────

class TestPayloadFromProduct:
    def test_basic_payload(self):
        p = _product(pid="42", price=1200.0)
        payload = OshishaCart._payload_from_product(p)
        assert payload is not None
        assert payload["ID"] == "42"
        assert payload["QUANTITY"] == 1
        assert payload["TYPE"] == "add"

    def test_price_uses_round_not_int(self):
        """round(1299.7) == 1300, int(1299.7) == 1299 — проверяем round."""
        p = _product(pid="1", price=1299.7)
        payload = OshishaCart._payload_from_product(p)
        assert payload is not None
        assert payload["PRICE"] == 1300   # round, не int

    def test_price_rounds_down(self):
        p = _product(pid="1", price=1299.2)
        payload = OshishaCart._payload_from_product(p)
        assert payload is not None
        assert payload["PRICE"] == 1299   # round(1299.2) == 1299

    def test_price_none_gives_zero(self):
        p = _product(pid="1", price=None)  # type: ignore[arg-type]
        payload = OshishaCart._payload_from_product(p)
        assert payload is not None
        assert payload["PRICE"] == 0

    def test_can_buy_false_returns_none(self):
        p = _product(pid="1", can_buy=False)
        assert OshishaCart._payload_from_product(p) is None

    def test_quantity_param(self):
        p = _product(pid="1", price=500.0)
        payload = OshishaCart._payload_from_product(p, quantity=3)
        assert payload["QUANTITY"] == 3
        assert payload["PRICE"] == 1500   # round(500) * 3


# ── OshishaCart._payload_from_check (static) ─────────────────────────────────

class TestPayloadFromCheck:
    def test_basic_payload(self):
        c = _check(pid="7", price=900.0)
        payload = OshishaCart._payload_from_check(c)
        assert payload is not None
        assert payload["ID"] == "7"
        assert payload["QUANTITY"] == 1

    def test_price_uses_round(self):
        c = _check(pid="1", price=899.6)
        payload = OshishaCart._payload_from_check(c)
        assert payload is not None
        assert payload["PRICE"] == 900   # round(899.6) == 900

    def test_pack_count_sets_quantity(self):
        c = _check(pid="1", pack_count=3, price=400.0)
        payload = OshishaCart._payload_from_check(c)
        assert payload["QUANTITY"] == 3
        assert payload["PRICE"] == 1200  # round(400) * 3

    def test_not_found_returns_none(self):
        c = _check(status="не найден", pid=None)
        assert OshishaCart._payload_from_check(c) is None

    def test_out_of_stock_returns_none(self):
        c = _check(status="нет")
        assert OshishaCart._payload_from_check(c) is None


# ── add_checks ────────────────────────────────────────────────────────────────

class TestAddChecks:
    def test_all_in_stock_returns_added(self):
        checks = [_check(pid="1"), _check(query="sarm visa", pid="2", matched_name="Sarma 200г")]
        cart = OshishaCart(_mock_auth(_add2basket_success(["1", "2"])))
        result = cart.add_checks(checks)

        assert result.added_count == 2
        assert all(item.success for item in result.items)
        assert all(item.message == "добавлено" for item in result.items)

    def test_not_found_pre_rejected(self):
        checks = [_check(status="не найден", pid=None), _check(pid="2")]
        cart = OshishaCart(_mock_auth(_add2basket_success(["2"])))
        result = cart.add_checks(checks)

        assert result.items[0].success is False
        assert result.items[0].message == "не найден"
        assert result.items[1].success is True

    def test_out_of_stock_pre_rejected(self):
        checks = [_check(status="нет", pid="1"), _check(pid="2")]
        cart = OshishaCart(_mock_auth(_add2basket_success(["2"])))
        result = cart.add_checks(checks)

        assert result.items[0].success is False
        assert "наличии" in result.items[0].message
        assert result.items[1].success is True

    def test_empty_checks_no_api_call(self):
        auth = _mock_auth()
        cart = OshishaCart(auth)
        result = cart.add_checks([])

        auth._client.post.assert_not_called()
        assert result.added_count == 0
        assert result.items == []

    def test_all_failures_no_api_call(self):
        checks = [_check(status="не найден", pid=None), _check(status="нет")]
        auth = _mock_auth()
        cart = OshishaCart(auth)
        result = cart.add_checks(checks)

        auth._client.post.assert_not_called()
        assert result.added_count == 0

    def test_mixed_order_preserved(self):
        """Порядок результатов должен совпадать с порядком входных checks."""
        checks = [
            _check(query="q1", pid="1"),
            _check(query="q2", status="нет"),
            _check(query="q3", pid="3"),
        ]
        cart = OshishaCart(_mock_auth(_add2basket_success(["1", "3"])))
        result = cart.add_checks(checks)

        assert result.items[0].query == "q1"
        assert result.items[1].query == "q2"
        assert result.items[2].query == "q3"
        assert result.items[0].success is True
        assert result.items[1].success is False
        assert result.items[2].success is True

    def test_cart_totals_populated(self):
        checks = [_check(pid="1")]
        api = _add2basket_success(["1"])
        api["QUANTITY"] = 5
        api["SUM_PRICE"] = 6000
        cart = OshishaCart(_mock_auth(api))
        result = cart.add_checks(checks)

        assert result.cart_quantity == 5
        assert result.cart_sum_price == 6000

    def test_custom_queries_used_as_labels(self):
        checks = [_check(query="original", pid="1")]
        cart = OshishaCart(_mock_auth(_add2basket_success(["1"])))
        result = cart.add_checks(checks, queries=["custom label"])

        assert result.items[0].query == "custom label"

    def test_api_error_status_raises(self):
        checks = [_check(pid="1")]
        resp = MagicMock()
        resp.json.return_value = {"STATUS": "error", "MESSAGE": "Session expired"}
        resp.text = '{"STATUS":"error"}'
        resp.raise_for_status.return_value = None
        auth = _mock_auth()
        auth._client.post.return_value = resp
        cart = OshishaCart(auth)

        with pytest.raises(OshishaAuthError):
            cart.add_checks(checks)

    def test_invalid_json_raises(self):
        checks = [_check(pid="1")]
        resp = MagicMock()
        resp.json.side_effect = json.JSONDecodeError("Bad", "", 0)
        resp.text = "not json"
        resp.raise_for_status.return_value = None
        auth = _mock_auth()
        auth._client.post.return_value = resp
        cart = OshishaCart(auth)

        with pytest.raises(OshishaAuthError):
            cart.add_checks(checks)


# ── add_from_products ─────────────────────────────────────────────────────────

class TestAddFromProducts:
    def test_adds_available_product(self):
        product = _product(pid="42")
        cart = OshishaCart(_mock_auth(_add2basket_success(["42"])))
        result = cart.add_from_products([(product, "my query", 1)])

        assert result.added_count == 1
        assert result.items[0].query == "my query"
        assert result.items[0].success is True

    def test_skips_can_buy_false(self):
        product = _product(pid="99", can_buy=False)
        auth = _mock_auth()
        cart = OshishaCart(auth)
        result = cart.add_from_products([(product, "test", 1)])

        auth._client.post.assert_not_called()
        assert result.added_count == 0
        assert "наличии" in result.items[0].message

    def test_empty_list_no_api_call(self):
        auth = _mock_auth()
        cart = OshishaCart(auth)
        result = cart.add_from_products([])

        auth._client.post.assert_not_called()
        assert result.items == []

    def test_multiple_quantities(self):
        products = [
            (_product(pid="1"), "label1", 2),
            (_product(pid="2"), "label2", 1),
        ]
        cart = OshishaCart(_mock_auth(_add2basket_success(["1", "2"])))
        result = cart.add_from_products(products)

        assert result.added_count == 2

    def test_price_rounded_in_payload(self):
        """_payload_from_product использует round(), проверяем через item.line_price
        когда API не возвращает PRICE для продукта."""
        product = _product(pid="1", price=1299.7)
        # Возвращаем ответ без PRICE в ITEMS, чтобы item.line_price брался из payload
        api = {
            "STATUS": "success",
            "ITEMS": {"1": {"NAME": "Test"}},  # нет PRICE → line_price из payload
            "QUANTITY": 1,
            "SUM_PRICE": 0,
        }
        cart = OshishaCart(_mock_auth(api))
        result = cart.add_from_products([(product, "test", 1)])

        # round(1299.7) * 1 == 1300; int(1299.7) * 1 == 1299
        assert result.items[0].line_price == 1300


# ── fetch_cart ────────────────────────────────────────────────────────────────

class TestFetchCart:
    def _basket_response(self, rows: list[dict], total: float = 0.0) -> dict:
        row_dict = {str(i): row for i, row in enumerate(rows)}
        return {
            "BASKET_DATA": {
                "EMPTY_BASKET": False,
                "GRID": {"ROWS": row_dict},
                "allSum": total or sum(r.get("SUM", 0) for r in rows),
                "allSum_FORMATED": f"{total} руб.",
            }
        }

    def test_returns_items(self):
        resp_data = self._basket_response([
            {"NAME": "BlackBurn Малина 200г", "QUANTITY": 2, "PRICE": 1200.0, "SUM": 2400.0},
            {"NAME": "Duft Клубника 100г",    "QUANTITY": 1, "PRICE": 900.0,  "SUM": 900.0},
        ], total=3300.0)
        cart = OshishaCart(_mock_auth(resp_data))
        result = cart.fetch_cart()

        assert result.empty is False
        assert len(result.items) == 2
        assert result.items[0].name == "BlackBurn Малина 200г"
        assert result.items[0].quantity == 2
        assert result.total_sum == 3300.0

    def test_empty_basket(self):
        resp_data = {"BASKET_DATA": {"EMPTY_BASKET": True}}
        cart = OshishaCart(_mock_auth(resp_data))
        result = cart.fetch_cart()

        assert result.empty is True
        assert result.items == []

    def test_rows_without_name_skipped(self):
        resp_data = self._basket_response([
            {"NAME": "", "QUANTITY": 1, "SUM": 100.0},
            {"NAME": "Valid Item", "QUANTITY": 1, "SUM": 500.0},
        ])
        cart = OshishaCart(_mock_auth(resp_data))
        result = cart.fetch_cart()

        assert len(result.items) == 1
        assert result.items[0].name == "Valid Item"

    def test_invalid_json_raises_auth_error(self):
        resp = MagicMock()
        resp.json.side_effect = json.JSONDecodeError("Nope", "", 0)
        resp.text = "not json"
        resp.raise_for_status.return_value = None
        auth = _mock_auth()
        auth._client.post.return_value = resp
        cart = OshishaCart(auth)

        with pytest.raises(OshishaAuthError):
            cart.fetch_cart()

    def test_sessid_passed_in_request(self):
        resp_data = {"BASKET_DATA": {"EMPTY_BASKET": True}}
        auth = _mock_auth(resp_data)
        auth.fetch_sessid.return_value = "my_sessid_xyz"
        cart = OshishaCart(auth)
        cart.fetch_cart()

        post_call = auth._client.post.call_args
        data = post_call.kwargs.get("data") or {}
        assert data.get("sessid") == "my_sessid_xyz"

    def test_sum_price_computed_from_price_quantity_when_sum_missing(self):
        """Если в строке нет SUM, он вычисляется как PRICE * QUANTITY."""
        resp_data = self._basket_response([
            {"NAME": "Test", "QUANTITY": 3, "PRICE": 400.0},  # нет SUM
        ])
        cart = OshishaCart(_mock_auth(resp_data))
        result = cart.fetch_cart()

        assert result.items[0].sum_price == pytest.approx(1200.0)

    def test_cart_url_set(self):
        resp_data = {"BASKET_DATA": {"EMPTY_BASKET": True}}
        cart = OshishaCart(_mock_auth(resp_data))
        result = cart.fetch_cart()

        assert "oshisha.cc" in result.cart_url or result.cart_url.startswith("/")
