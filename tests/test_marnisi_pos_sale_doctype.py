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

    def sql_ddl(self, query):
        return self.sql(query)

    def commit(self):
        self.commit_calls += 1


class _BackfillDB(_FakeDB):
    def sql(self, query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))
        if "FROM `tabMarnisi POS Sale`" in normalized and as_dict:
            return [
                {
                    "name": "MPS-OLD-001",
                    "sale_num": "OLD-001",
                    "sales_payload": json.dumps(
                        {
                            "sales_num": "OLD-001",
                            "items": [
                                {
                                    "si_sale_num": "OLD-001",
                                    "si_id": "ITEM-OLD",
                                    "si_name": "Legacy Item",
                                    "si_qty": 1,
                                    "si_price": 10,
                                    "si_tax_pct": 18,
                                    "si_subtotal": 10,
                                    "si_tax": 1.8,
                                    "si_total": 11.8,
                                }
                            ],
                            "sale_pay_methods": [
                                {
                                    "tender_type_id": "1",
                                    "payment_name": "Cash",
                                    "amount_tendered": 11.8,
                                }
                            ],
                        }
                    ),
                }
            ]
        return []


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


class _FakeBackfillFrappe(_FakeFrappe):
    def __init__(self):
        self.db = _BackfillDB()
        self.utils = _FakeUtils()
        self.session = type("Session", (), {"user": "marnisi.admin.north@example.com"})()


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


def test_post_all_sales_routes_frozen_payload_to_atomic_service(monkeypatch):
    from xirja_marnisi.fiscal import service
    payload = {'sales': [{'sales_num':'stable-sale', 'fiscal_protocol':1}]}
    calls = []
    monkeypatch.setattr(service, 'ingest_batch', lambda rows: calls.append(rows) or {'confirmations':[]})
    assert bridge.post_all_sales(json.dumps(payload)) == {'confirmations':[]}
    assert calls == [payload]


def test_request_time_backfill_is_disabled():
    import pytest
    with pytest.raises(ValueError, match='migration-only'):
        bridge.backfill_sales_children('{}')


def test_backfill_sales_children_populates_child_tables(monkeypatch):
    fake_frappe = _FakeBackfillFrappe()
    monkeypatch.setattr(bridge, "frappe", fake_frappe)
    monkeypatch.setattr(bridge, "require_authenticated_user", lambda: "admin@example.com")
    monkeypatch.setattr(bridge, "parse_args", lambda _args: {"limit": 0})

    result = bridge._backfill_sales_children_from_payload(limit=0)
    assert result["processed"] == 1
    assert result["scanned"] == 1

    sale_item_calls = [
        call
        for call in fake_frappe.db.calls
        if "INSERT INTO `tabMarnisi POS Sale Item`" in call[0]
    ]
    sale_payment_calls = [
        call
        for call in fake_frappe.db.calls
        if "INSERT INTO `tabMarnisi POS Sale Payment`" in call[0]
    ]
    assert len(sale_item_calls) == 1
    assert len(sale_payment_calls) == 1
