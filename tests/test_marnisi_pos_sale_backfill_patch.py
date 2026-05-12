from __future__ import annotations

from xirja_marnisi.api import bridge
from xirja_marnisi.patches.v0_0_1 import backfill_marnisi_pos_sale_children


class _FakeDB:
    def __init__(self):
        self.commit_calls = 0

    def commit(self):
        self.commit_calls += 1


class _FakeFrappe:
    def __init__(self):
        self.db = _FakeDB()


def test_backfill_patch_runs_child_backfill_and_commit(monkeypatch):
    fake_frappe = _FakeFrappe()
    captured = {"limit": None}

    def _fake_backfill(limit: int = 0):
        captured["limit"] = limit
        return {"scanned": 0, "processed": 0}

    monkeypatch.setattr(backfill_marnisi_pos_sale_children, "frappe", fake_frappe)
    monkeypatch.setattr(bridge, "_backfill_sales_children_from_payload", _fake_backfill)

    backfill_marnisi_pos_sale_children.execute()

    assert captured["limit"] == 0
    assert fake_frappe.db.commit_calls == 1
