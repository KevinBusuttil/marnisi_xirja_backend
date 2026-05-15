from __future__ import annotations

from xirja_marnisi.api import seed


class _FakeDB:
    def __init__(self):
        self.calls: list[tuple[str, tuple | None]] = []

    def sql(self, query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        self.calls.append((normalized, params))

        if "SELECT DISTINCT `user` FROM `tabVineyard User Access`" in normalized:
            return [
                {"user": "marnisi.admin.north@example.com"},
                {"user": "marnisi.staff@example.com"},
            ]

        if "SELECT access_role FROM `tabVineyard User Access`" in normalized:
            user = (params or ("",))[0]
            if user == "marnisi.admin.north@example.com":
                return [{"access_role": "Vineyard Admin"}]
            return [{"access_role": "Vineyard Staff"}]

        return []


class _FakeUtils:
    @staticmethod
    def now():
        return "2026-05-14 00:00:00.000000"


class _FakeFrappe:
    def __init__(self):
        self.db = _FakeDB()
        self.utils = _FakeUtils()
        self.session = type("Session", (), {"user": "Administrator"})()


def test_enforce_single_store_setup_creates_single_vineyard_access(monkeypatch):
    fake_frappe = _FakeFrappe()
    monkeypatch.setattr(seed, "frappe", fake_frappe)
    monkeypatch.setattr(
        seed,
        "_ensure_vineyard",
        lambda _code, _name, _timezone: "Marnisi M'Xlokk",
    )

    ensured_access_calls = []

    def _fake_ensure_access(*, user, vineyard, access_role, is_default):
        ensured_access_calls.append((user, vineyard, access_role, is_default))

    monkeypatch.setattr(seed, "_ensure_access", _fake_ensure_access)

    result = seed._enforce_single_store_setup(deactivate_other_vineyards=True)

    assert result["status"] == "success"
    assert result["vineyard"] == "Marnisi M'Xlokk"
    assert result["register_id"] == "Marnisi M'Xlokk-MAIN"
    assert result["access_users_updated"] == 2

    assert (
        "marnisi.admin.north@example.com",
        "Marnisi M'Xlokk",
        "Vineyard Admin",
        1,
    ) in ensured_access_calls
    assert (
        "marnisi.staff@example.com",
        "Marnisi M'Xlokk",
        "Vineyard Staff",
        1,
    ) in ensured_access_calls

    update_queries = [query for query, _params in fake_frappe.db.calls]
    assert any("UPDATE `tabVineyard` SET is_active = 0" in query for query in update_queries)
    assert any(
        "UPDATE `tabVineyard User Access` SET is_default = 0, is_active = 0" in query
        for query in update_queries
    )


def test_enforce_single_store_setup_can_keep_other_vineyards_active(monkeypatch):
    fake_frappe = _FakeFrappe()
    monkeypatch.setattr(seed, "frappe", fake_frappe)
    monkeypatch.setattr(
        seed,
        "_ensure_vineyard",
        lambda _code, _name, _timezone: "Marnisi M'Xlokk",
    )
    monkeypatch.setattr(seed, "_ensure_access", lambda **_kwargs: None)

    result = seed._enforce_single_store_setup(deactivate_other_vineyards=False)

    assert result["deactivated_other_vineyards"] is False
    update_queries = [query for query, _params in fake_frappe.db.calls]
    assert not any("UPDATE `tabVineyard` SET is_active = 0" in query for query in update_queries)
