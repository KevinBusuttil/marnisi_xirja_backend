from __future__ import annotations

import pytest

from xirja_marnisi.api import item


class _FakeDB:
    def __init__(self, *, stock_qty: float = 10.0):
        self.stock_qty = stock_qty
        self.queries: list[str] = []
        self.updated_stock: float | None = None
        self.committed = False
        self.rolled_back = False

    def sql(self, query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        self.queries.append(normalized)

        if "FROM `tabVineyard Item`" in normalized and "FOR UPDATE" in normalized:
            return [
                {
                    "name": "ITEM-1",
                    "vineyard": "VYD-NORTH",
                    "stock_qty": self.stock_qty,
                }
            ]

        if normalized.startswith("UPDATE `tabVineyard Item`"):
            self.updated_stock = float(params[0])
            self.stock_qty = float(params[0])
            return []

        return []

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeUtils:
    @staticmethod
    def now():
        return "2026-05-03 12:00:00"


class _FakeFrappe:
    def __init__(self, db):
        self.db = db
        self.utils = _FakeUtils()

    @staticmethod
    def throw(message: str, exc=None):
        error_type = exc or Exception
        raise error_type(message)


def _patch_common(monkeypatch, fake_frappe, payload):
    monkeypatch.setattr(item, "frappe", fake_frappe)
    monkeypatch.setattr(item, "parse_args", lambda _args: payload)
    monkeypatch.setattr(item, "require_authenticated_user", lambda: "admin@example.com")
    monkeypatch.setattr(item, "require_vineyard_permission", lambda _vineyard, mutate=True: None)


def test_adjust_stock_delta_updates_stock_and_writes_movement(monkeypatch):
    db = _FakeDB(stock_qty=12)
    fake_frappe = _FakeFrappe(db)

    movements: list[dict] = []

    _patch_common(
        monkeypatch,
        fake_frappe,
        {
            "item_id": "ITEM-1",
            "mode": "delta",
            "delta_qty": -2,
            "reason": "Serve tasting",
        },
    )
    monkeypatch.setattr(item, "_insert_stock_movement", lambda **kwargs: movements.append(kwargs))

    response = item.adjust_stock()

    assert response["status"] == "success"
    assert response["stock_qty"] == 10
    assert db.updated_stock == 10
    assert db.committed is True
    assert db.rolled_back is False
    assert any("FOR UPDATE" in query for query in db.queries)
    assert movements and movements[0]["movement_type"] == "ADJUST_DELTA"


def test_adjust_stock_set_mode(monkeypatch):
    db = _FakeDB(stock_qty=4)
    fake_frappe = _FakeFrappe(db)
    movements: list[dict] = []

    _patch_common(
        monkeypatch,
        fake_frappe,
        {
            "item_id": "ITEM-1",
            "mode": "set",
            "set_qty": 17,
            "reason": "End-of-day recount",
        },
    )
    monkeypatch.setattr(item, "_insert_stock_movement", lambda **kwargs: movements.append(kwargs))

    response = item.adjust_stock()

    assert response["stock_qty"] == 17
    assert db.updated_stock == 17
    assert movements[0]["movement_type"] == "ADJUST_SET"


def test_adjust_stock_prevents_negative_stock_and_rolls_back(monkeypatch):
    db = _FakeDB(stock_qty=1)
    fake_frappe = _FakeFrappe(db)

    _patch_common(
        monkeypatch,
        fake_frappe,
        {
            "item_id": "ITEM-1",
            "mode": "delta",
            "delta_qty": -5,
            "reason": "Invalid",
        },
    )

    with pytest.raises(Exception, match="Stock cannot be negative"):
        item.adjust_stock()

    assert db.committed is False
    assert db.rolled_back is True
