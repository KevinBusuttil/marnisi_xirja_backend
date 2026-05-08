from __future__ import annotations

import pytest

from xirja_marnisi.api import booking


class _FakeDB:
    def __init__(self, *, status: str, stock_deducted: int):
        self.status = status
        self.stock_deducted = stock_deducted
        self.queries: list[str] = []
        self.committed = False
        self.rolled_back = False

    def sql(self, query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        self.queries.append(normalized)

        if "FROM `tabTour Booking`" in normalized and "FOR UPDATE" in normalized:
            return [
                {
                    "name": "TB-001",
                    "vineyard": "VYD-NORTH",
                    "tour_package": "PKG-1",
                    "participants_count": 4,
                    "status": self.status,
                    "stock_deducted": self.stock_deducted,
                }
            ]

        if normalized.startswith("UPDATE `tabTour Booking`"):
            self.status = params[0]
            self.stock_deducted = int(params[1])
            return []

        return []

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _FakeUtils:
    @staticmethod
    def now():
        return "2026-05-03 13:00:00"


class _FakeFrappe:
    class PermissionError(Exception):
        pass

    def __init__(self, db):
        self.db = db
        self.utils = _FakeUtils()

    @staticmethod
    def throw(message: str, exc=None):
        error_type = exc or Exception
        raise error_type(message)


def _patch_common(monkeypatch, fake_frappe, payload):
    monkeypatch.setattr(booking, "frappe", fake_frappe)
    monkeypatch.setattr(booking, "parse_args", lambda _args: payload)
    monkeypatch.setattr(booking, "require_authenticated_user", lambda: "staff@example.com")
    monkeypatch.setattr(
        booking,
        "require_vineyard_permission",
        lambda _vineyard, mutate=True, staff_allowed=True: None,
    )


def test_update_status_to_checked_in_deducts_stock_once(monkeypatch):
    db = _FakeDB(status="CONFIRMED", stock_deducted=0)
    fake_frappe = _FakeFrappe(db)
    stock_calls: list[dict] = []

    _patch_common(
        monkeypatch,
        fake_frappe,
        {
            "booking_id": "TB-001",
            "status": "CHECKED_IN",
        },
    )
    monkeypatch.setattr(booking, "_deduct_or_restore_stock", lambda **kwargs: stock_calls.append(kwargs))

    result = booking.update_status()

    assert result["status"] == "success"
    assert result["status_value"] == "CHECKED_IN"
    assert result["stock_deducted"] == 1
    assert len(stock_calls) == 1
    assert stock_calls[0]["restore"] is False
    assert any("FOR UPDATE" in query for query in db.queries)
    assert db.committed is True
    assert db.rolled_back is False


def test_update_status_cancelled_restores_deducted_stock(monkeypatch):
    db = _FakeDB(status="CHECKED_IN", stock_deducted=1)
    fake_frappe = _FakeFrappe(db)
    stock_calls: list[dict] = []

    _patch_common(
        monkeypatch,
        fake_frappe,
        {
            "booking_id": "TB-001",
            "status": "CANCELLED",
            "cancel_reason": "Guest no-show",
        },
    )
    monkeypatch.setattr(booking, "_deduct_or_restore_stock", lambda **kwargs: stock_calls.append(kwargs))

    result = booking.update_status()

    assert result["status_value"] == "CANCELLED"
    assert result["stock_deducted"] == 0
    assert len(stock_calls) == 1
    assert stock_calls[0]["restore"] is True


def test_update_status_invalid_transition_rolls_back(monkeypatch):
    db = _FakeDB(status="DRAFT", stock_deducted=0)
    fake_frappe = _FakeFrappe(db)

    _patch_common(
        monkeypatch,
        fake_frappe,
        {
            "booking_id": "TB-001",
            "status": "COMPLETED",
        },
    )

    with pytest.raises(Exception, match="Invalid transition"):
        booking.update_status()

    assert db.committed is False
    assert db.rolled_back is True
