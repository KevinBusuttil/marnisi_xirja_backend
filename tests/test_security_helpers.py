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
