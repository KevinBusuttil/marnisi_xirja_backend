from __future__ import annotations

import json

import pytest

from xirja_marnisi.api import security


class _ThrowingFrappe:
    class PermissionError(Exception):
        pass

    @staticmethod
    def throw(message: str, exc: type[Exception] | None = None):
        err = exc or Exception
        raise err(message)


def test_parse_args_prefers_nested_args_json():
    payload = {
        "args": json.dumps(
            {
                "vineyard": "VYD-NORTH",
                "enabled": True,
            }
        )
    }

    parsed = security.parse_args(payload)

    assert parsed == {"vineyard": "VYD-NORTH", "enabled": True}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("yes", True),
        ("TRUE", True),
        ("0", False),
        ("off", False),
    ],
)
def test_to_bool(value, expected):
    assert security.to_bool(value) is expected


def test_resolve_vineyard_uses_default_assignment(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_accessible_vineyards",
        lambda _user: [
            {"vineyard": "VYD-SOUTH", "is_default": 0},
            {"vineyard": "VYD-NORTH", "is_default": 1},
        ],
    )

    resolved = security.resolve_vineyard({}, "staff@example.com")

    assert resolved == "VYD-NORTH"


def test_resolve_vineyard_rejects_unassigned_request(monkeypatch):
    monkeypatch.setattr(
        security,
        "get_accessible_vineyards",
        lambda _user: [{"vineyard": "VYD-NORTH", "is_default": 1}],
    )
    monkeypatch.setattr(security, "frappe", _ThrowingFrappe())

    with pytest.raises(_ThrowingFrappe.PermissionError):
        security.resolve_vineyard({"vineyard": "VYD-SOUTH"}, "viewer@example.com")


@pytest.mark.parametrize("user,enabled,allowed", [("Guest",1,False), ("staff@example.com",0,False), ("staff@example.com",1,True)])
def test_enabled_session_required(monkeypatch,user,enabled,allowed):
    from types import SimpleNamespace
    fake = _ThrowingFrappe()
    fake.AuthenticationError = fake.PermissionError
    fake.session = SimpleNamespace(user=user)
    fake.db = SimpleNamespace(sql=lambda query,values: [(enabled,)])
    monkeypatch.setattr(security, "frappe", fake)
    if allowed:
        assert security.require_authenticated_user() == user
    else:
        with pytest.raises(fake.AuthenticationError):
            security.require_authenticated_user()


@pytest.mark.parametrize("role,staff_allowed,allowed", [
    ("Viewer",True,False), ("Vineyard Staff",True,True),
    ("Vineyard Staff",False,False), ("Vineyard Admin",False,True),
    ("Vineyard Manager",False,True),
])
def test_vineyard_role_controls_mutation(monkeypatch,role,staff_allowed,allowed):
    monkeypatch.setattr(security, "frappe", _ThrowingFrappe())
    monkeypatch.setattr(security, "require_authenticated_user", lambda: "staff@example.com")
    monkeypatch.setattr(security, "is_global_admin", lambda user: False)
    monkeypatch.setattr(security, "get_access_rows", lambda user: [{"vineyard":security.LOCKED_STORE_ID,"access_role":role}])
    if allowed:
        assert security.require_pos_access(mutate=True, staff_allowed=staff_allowed)["role"] == role
    else:
        with pytest.raises(_ThrowingFrappe.PermissionError):
            security.require_pos_access(mutate=True, staff_allowed=staff_allowed)


@pytest.mark.parametrize("kwargs", [{"store":"other"},{"register":"other"}])
def test_pos_rejects_wrong_store_and_register(monkeypatch,kwargs):
    monkeypatch.setattr(security, "frappe", _ThrowingFrappe())
    with pytest.raises(_ThrowingFrappe.PermissionError):
        security.require_pos_access(**kwargs)
