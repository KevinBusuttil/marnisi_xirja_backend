from __future__ import annotations

import copy
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "update_pos_prices", Path(__file__).resolve().parents[1] / "scripts" / "update_pos_prices.py"
)
prices = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prices)


@pytest.mark.parametrize("gross,net", [
    ("85.00", "72.034"), ("100.00", "84.746"), ("115.00", "97.458"),
    ("20.75", "17.585"), ("56.25", "47.669"), ("30.60", "25.932"), ("29.90", "25.339"),
])
def test_three_decimal_sell_price_round_trips_to_exact_requested_price(gross, net):
    assert prices.sell_price(gross, 18) == Decimal(net)
    assert prices.gross_price(net, 18) == Decimal(gross)


@pytest.mark.parametrize("gross,vat", [("NaN", 18), ("Infinity", 18), (-1, 18), (1, -1), ("1.001", 18)])
def test_invalid_prices_are_rejected(gross, vat):
    with pytest.raises(ValueError):
        prices.sell_price(gross, vat)


def catalog():
    items, products = [], []
    for index, (code, label, _) in enumerate(prices.REQUESTS):
        items.append({
            "name": f"ITEM-{index}", "vineyard": prices.VINEYARD,
            "item_code": code + " ", "item_name": label + " ", "sell_price": 10,
            "modified": "2026-09-15 10:00:00.000000", "stock_qty": 1000,
            "image_path": "/private/files/wine.png", "is_enabled": 1,
        })
        products.append({
            "item_id": f"{prices.VINEYARD}::{code}", "item_store": prices.VINEYARD,
            "item_name": label, "item_price": 10, "item_tax_pct": 18,
            "item_qty": 1000, "item_img_path": "/private/files/wine.png",
        })
    return items, products


def test_plan_matches_all_nine_and_ignores_other_vineyards():
    items, products = catalog()
    other = dict(items[0], name="OTHER", vineyard="Another Vineyard")
    plan = prices.build_plan(items + [other], products)
    assert len(plan) == 9
    assert plan[0]["sell_price"] == "72.034"
    assert plan[-1]["pos_price"] == "29.90"


@pytest.mark.parametrize("change", ["missing", "duplicate", "renamed", "wrong_vineyard", "wrong_vat", "missing_pos", "price_disagrees"])
def test_plan_refuses_ambiguous_or_changed_catalog(change):
    items, products = catalog()
    if change == "missing":
        items.pop()
    elif change == "duplicate":
        items.append(dict(items[0], name="DUPLICATE"))
    elif change == "renamed":
        items[0]["item_name"] = "Gift Box Variant"
    elif change == "wrong_vineyard":
        items[0]["vineyard"] = "Other Vineyard"
    elif change == "wrong_vat":
        products[0]["item_tax_pct"] = 5
    elif change == "missing_pos":
        products.pop()
    else:
        products[0]["item_price"] = 20
    with pytest.raises(ValueError):
        prices.build_plan(items, products)


class FakeClient:
    base_url = "https://example.test"

    def __init__(self, items):
        self.documents = {item["name"]: copy.deepcopy(item) for item in items}
        self.writes = []

    def document(self, name):
        return copy.deepcopy(self.documents[name])

    def set_price(self, row):
        self.writes.append(row["name"])
        self.documents[row["name"]]["sell_price"] = float(row["sell_price"])


def test_apply_backs_up_and_changes_only_prices(tmp_path):
    items, products = catalog()
    client = FakeClient(items)
    plan = prices.build_plan(items, products)
    backup = tmp_path / "before.json"
    prices.apply_plan(client, plan, backup)
    assert len(client.writes) == 9
    assert json.loads(backup.read_text())["documents"]["ITEM-0"]["sell_price"] == 10
    for item in items:
        saved = client.document(item["name"])
        assert {**saved, "sell_price": item["sell_price"]} == item


def test_all_documents_checked_before_any_write(tmp_path):
    items, products = catalog()
    client = FakeClient(items)
    client.documents[items[-1]["name"]]["modified"] = "new timestamp"
    with pytest.raises(ValueError, match="changed since preview"):
        prices.apply_plan(client, prices.build_plan(items, products), tmp_path / "before.json")
    assert client.writes == []


def test_existing_backup_not_overwritten(tmp_path):
    items, products = catalog()
    client = FakeClient(items)
    backup = tmp_path / "before.json"
    backup.write_text("original backup")
    with pytest.raises(FileExistsError):
        prices.apply_plan(client, prices.build_plan(items, products), backup)
    assert backup.read_text() == "original backup"
    assert client.writes == []


def test_already_correct_prices_do_not_write_again(tmp_path):
    items, products = catalog()
    for item, product, (_, _, gross) in zip(items, products, prices.REQUESTS):
        item["sell_price"] = product["item_price"] = float(prices.sell_price(gross, 18))
    client = FakeClient(items)
    prices.apply_plan(client, prices.build_plan(items, products), tmp_path / "before.json")
    assert client.writes == []


def test_transport_sends_only_price_and_optimistic_timestamp(monkeypatch):
    client = prices.Client("https://example.test")
    calls = []
    monkeypatch.setattr(client, "request", lambda *args: calls.append(args))
    client.set_price({"name": "wine", "sell_price": "17.585", "modified": "timestamp"})
    assert calls == [("/api/resource/Vineyard%20Item/wine", {"sell_price": 17.585, "modified": "timestamp"}, "PUT")]


def test_live_feed_verification_detects_wrong_total_and_untargeted_changes():
    items, before = catalog()
    before.append({"item_id": "UNRELATED", "item_price": 24.45})
    plan = prices.build_plan(items, before)
    after = copy.deepcopy(before)
    for row, target in zip(after, plan):
        row["item_price"] = float(target["sell_price"])
    prices.verify_pos(plan, before, after)
    after[-1]["item_price"] = 99
    with pytest.raises(ValueError, match="Unexpected POS"):
        prices.verify_pos(plan, before, after)
    after[-1]["item_price"] = 24.45
    after[0]["item_price"] = 85  # Incorrectly storing the inclusive target as net.
    with pytest.raises(ValueError, match="Final POS price mismatch"):
        prices.verify_pos(plan, before, after)


def test_login_requires_own_password_and_captures_csrf(monkeypatch):
    client = prices.Client("https://example.test")
    calls = []
    def call(method, args):
        calls.append((method, args))
        return {"user": "test@example.invalid", "csrf_token": "test-csrf"}
    monkeypatch.setattr(client, "call", call)
    client.login("test-id", "test-only-password")
    assert calls == [("xirja_marnisi.api.auth.login_with_personal_id", {
        "personal_id": "test-id", "password": "test-only-password"})]
    assert client.csrf_token == "test-csrf"


def test_price_updater_rejects_insecure_origin_and_redirects():
    for origin in ("http://example.test", "https://user:pass@example.test", "https://example.test?q=1"):
        with pytest.raises(ValueError):prices.Client(origin)
    with pytest.raises(ValueError):prices.Client("http://127.0.0.1")
    assert prices.Client("http://127.0.0.1", allow_loopback_http=True).base_url == "http://127.0.0.1"
    with pytest.raises(ValueError):prices.NoRedirect().redirect_request(None)


def test_authenticated_price_write_carries_csrf(monkeypatch):
    import io
    client = prices.Client("https://example.test")
    client.csrf_token = "test-csrf"
    captured = []
    def open_request(request, timeout):
        captured.append(request)
        return io.BytesIO(b'{"data":{}}')
    monkeypatch.setattr(client.opener, "open", open_request)
    client.set_price({"name":"wine","sell_price":"17.585","modified":"timestamp"})
    assert captured[0].get_header("X-frappe-csrf-token") == "test-csrf"
    assert json.loads(captured[0].data) == {"sell_price":17.585,"modified":"timestamp"}
