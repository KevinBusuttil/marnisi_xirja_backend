from __future__ import annotations

from xirja_marnisi.api import auth


class _FakeDB:
    def __init__(self, *, has_login_column: bool, has_app_column: bool):
        self.has_login_column = has_login_column
        self.has_app_column = has_app_column

    def sql(self, query: str, values=None, as_dict: bool = False):
        normalized = " ".join(query.split())

        if "FROM information_schema.COLUMNS" in normalized:
            _table_name, column_name = values
            if column_name == "pos_login_background_image":
                return [(1,)] if self.has_login_column else []
            if column_name == "pos_app_background_image":
                return [(1,)] if self.has_app_column else []
            return []

        if "FROM `tabVineyard`" in normalized:
            if as_dict:
                return [
                    {
                        "login_background_image": (
                            "/files/marnisi-login.jpg" if self.has_login_column else ""
                        ),
                        "app_background_image": (
                            "/files/marnisi-app.jpg" if self.has_app_column else ""
                        ),
                    }
                ]
            return []

        return []


class _FakeFrappe:
    def __init__(self, *, has_login_column: bool, has_app_column: bool):
        self.db = _FakeDB(
            has_login_column=has_login_column,
            has_app_column=has_app_column,
        )


def test_get_context_returns_ui_assets_when_columns_exist(monkeypatch):
    monkeypatch.setattr(
        auth,
        "frappe",
        _FakeFrappe(has_login_column=True, has_app_column=True),
    )
    monkeypatch.setattr(auth, "require_authenticated_user", lambda: "admin@example.com")
    monkeypatch.setattr(auth, "get_roles", lambda _user: {"Vineyard Admin"})
    monkeypatch.setattr(
        auth,
        "get_accessible_vineyards",
        lambda _user: [{"vineyard": "Marnisi M'Xlokk", "is_default": 1}],
    )
    monkeypatch.setattr(
        auth,
        "_load_receipt_settings",
        lambda: {"receipt_currency_label": "EUR"},
    )

    result = auth.get_context()

    assert result["ui_assets"]["login_background_image"] == "/files/marnisi-login.jpg"
    assert result["ui_assets"]["app_background_image"] == "/files/marnisi-app.jpg"
    assert result["receipt_settings"]["receipt_currency_label"] == "EUR"


def test_get_context_ui_assets_fallback_to_empty_when_columns_missing(monkeypatch):
    monkeypatch.setattr(
        auth,
        "frappe",
        _FakeFrappe(has_login_column=False, has_app_column=False),
    )
    monkeypatch.setattr(auth, "require_authenticated_user", lambda: "admin@example.com")
    monkeypatch.setattr(auth, "get_roles", lambda _user: {"Vineyard Admin"})
    monkeypatch.setattr(
        auth,
        "get_accessible_vineyards",
        lambda _user: [{"vineyard": "Marnisi M'Xlokk", "is_default": 1}],
    )
    monkeypatch.setattr(
        auth,
        "_load_receipt_settings",
        lambda: {"receipt_currency_label": "EUR"},
    )

    result = auth.get_context()

    assert result["ui_assets"] == {
        "login_background_image": "",
        "app_background_image": "",
    }
    assert result["receipt_settings"]["receipt_currency_label"] == "EUR"
