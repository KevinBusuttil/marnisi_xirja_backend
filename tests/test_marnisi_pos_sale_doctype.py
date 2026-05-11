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
        "sales_payload",
    }.issubset(fieldnames)


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
                "items": [],
                "sale_pay_methods": [],
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
    assert fake_frappe.db.commit_calls == 1
