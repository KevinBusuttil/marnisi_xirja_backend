from __future__ import annotations

import json
from pathlib import Path

from xirja_marnisi.api import bridge


def _doctype_json_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "xirja_marnisi"
        / "xirja_marnisi"
        / "doctype"
        / "marnisi_pos_sale"
        / "marnisi_pos_sale.json"
    )


def _sale_item_doctype_json_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "xirja_marnisi"
        / "xirja_marnisi"
        / "doctype"
        / "marnisi_pos_sale_item"
        / "marnisi_pos_sale_item.json"
    )


def _sale_payment_doctype_json_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "xirja_marnisi"
        / "xirja_marnisi"
        / "doctype"
        / "marnisi_pos_sale_payment"
        / "marnisi_pos_sale_payment.json"
    )


def test_marnisi_pos_sale_doctype_metadata_exists():
    doctype_path = _doctype_json_path()
    assert doctype_path.exists()

    data = json.loads(doctype_path.read_text())
    assert data["name"] == "Marnisi POS Sale"
    assert data["module"] == "XIRJA Marnisi"

    fieldnames = {row["fieldname"] for row in data["fields"]}
    assert {
        "sale_num",
        "sales_store",
        "sales_register_id",
        "sales_date",
        "sales_time",
        "sales_cashier",
        "sales_total",
        "sales_items",
        "sales_payments",
        "sales_payload",
    }.issubset(fieldnames)


def test_marnisi_pos_sale_child_doctypes_exist():
    item_data = json.loads(_sale_item_doctype_json_path().read_text())
    payment_data = json.loads(_sale_payment_doctype_json_path().read_text())

    assert item_data["name"] == "Marnisi POS Sale Item"
    assert item_data["istable"] == 1
    assert payment_data["name"] == "Marnisi POS Sale Payment"
    assert payment_data["istable"] == 1


class _FakeDB:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []
        self.commit_calls = 0

    def sql(self, query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))
        return []

    def commit(self):
        self.commit_calls += 1


class _FakeUtils:
    @staticmethod
    def now():
        return "2026-05-11 12:00:00.000000"


class _FakeFrappe:
    def __init__(self):
        self.db = _FakeDB()
        self.utils = _FakeUtils()
        self.session = type("Session", (), {"user": "marnisi.admin.north@example.com"})()

    @staticmethod
    def generate_hash(length=12):
        return "A" * length


def test_ensure_sales_tables_backfills_standard_frappe_columns(monkeypatch):
    fake_frappe = _FakeFrappe()
    monkeypatch.setattr(bridge, "frappe", fake_frappe)

    bridge._ensure_sales_tables()

    alter_statements = [
        query
        for query, _params in fake_frappe.db.calls
        if query.startswith("ALTER TABLE `tabMarnisi POS Sale` ADD COLUMN")
    ]

    assert any("ADD COLUMN idx INT NOT NULL DEFAULT 0" in query for query in alter_statements)
    assert any("ADD COLUMN _user_tags LONGTEXT" in query for query in alter_statements)
    assert any("ADD COLUMN _comments LONGTEXT" in query for query in alter_statements)
    assert any("ADD COLUMN _assign LONGTEXT" in query for query in alter_statements)
    assert any("ADD COLUMN _liked_by LONGTEXT" in query for query in alter_statements)

    create_statements = [query for query, _params in fake_frappe.db.calls if query.startswith("CREATE TABLE IF NOT EXISTS")]
    assert any("`tabMarnisi POS Sale Item`" in query for query in create_statements)
    assert any("`tabMarnisi POS Sale Payment`" in query for query in create_statements)


def test_post_all_sales_inserts_into_marnisi_pos_sale_table(monkeypatch):
    fake_frappe = _FakeFrappe()
    monkeypatch.setattr(bridge, "frappe", fake_frappe)

    payload = {
        "sales": [
            {
                "sales_num": "UNIT-001",
                "sales_store": "VYD-NORTH",
                "sales_registerId": "VYD-NORTH-MAIN",
                "sales_date": "2026-05-11",
                "sales_time": "10:20:30",
                "sales_cashier": "11111",
                "sales_total": 99.5,
                "loy_cust_card_num": "CARD-1",
                "items": [
                    {
                        "si_sale_num": "UNIT-001",
                        "si_id": "VYD-NORTH::ITEM-1",
                        "si_name": "Marnisi Red",
                        "si_qty": 2,
                        "si_price": 12.5,
                        "si_tax_pct": 18,
                        "si_subtotal": 25.0,
                        "si_tax": 4.5,
                        "si_total": 29.5,
                        "si_discount_amount": 0,
                        "si_discount_percent": 0,
                    }
                ],
                "sale_pay_methods": [
                    {
                        "tender_type_id": "1",
                        "payment_name": "Cash",
                        "amount_tendered": 99.5,
                    }
                ],
            }
        ]
    }

    result = bridge.post_all_sales(args=json.dumps(payload))

    assert result["confirmations"] == [
        {
            "sale_num": "UNIT-001",
            "status": "synchronized",
            "loy_cust_card_num": "CARD-1",
        }
    ]

    insert_calls = [call for call in fake_frappe.db.calls if "INSERT INTO `tabMarnisi POS Sale`" in call[0]]
    assert len(insert_calls) == 1

    _, params = insert_calls[0]
    assert params is not None
    assert params[5] == "UNIT-001"
    assert params[6] == "VYD-NORTH"
    assert params[7] == "VYD-NORTH-MAIN"
    assert params[10] == "11111"
    assert params[11] == 99.5

    sale_item_calls = [
        call
        for call in fake_frappe.db.calls
        if "INSERT INTO `tabMarnisi POS Sale Item`" in call[0]
    ]
    assert len(sale_item_calls) == 1

    _, item_params = sale_item_calls[0]
    assert item_params is not None
    assert item_params[5] == "MPS-UNIT-001"
    assert item_params[6] == "sales_items"
    assert item_params[9] == "UNIT-001"
    assert item_params[10] == "VYD-NORTH::ITEM-1"
    assert item_params[11] == "Marnisi Red"
    assert item_params[21] == 0.0

    sale_payment_calls = [
        call
        for call in fake_frappe.db.calls
        if "INSERT INTO `tabMarnisi POS Sale Payment`" in call[0]
    ]
    assert len(sale_payment_calls) == 1

    _, payment_params = sale_payment_calls[0]
    assert payment_params is not None
    assert payment_params[5] == "MPS-UNIT-001"
    assert payment_params[6] == "sales_payments"
    assert payment_params[9] == "UNIT-001"
    assert payment_params[10] == "1"
    assert payment_params[11] == "Cash"
    assert payment_params[12] == 99.5

    delete_item_calls = [
        call
        for call in fake_frappe.db.calls
        if "DELETE FROM `tabMarnisi POS Sale Item` WHERE parent = %s" in call[0]
    ]
    delete_payment_calls = [
        call
        for call in fake_frappe.db.calls
        if "DELETE FROM `tabMarnisi POS Sale Payment` WHERE parent = %s" in call[0]
    ]
    assert len(delete_item_calls) == 1
    assert len(delete_payment_calls) == 1

    assert fake_frappe.db.commit_calls == 1
