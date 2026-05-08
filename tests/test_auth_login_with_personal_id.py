from __future__ import annotations

import pytest

from xirja_marnisi.api import auth


class _FakeLoginManager:
    def __init__(self):
        self.auth_calls: list[tuple[str, str]] = []
        self.post_login_called = False

    def authenticate(self, user: str, pwd: str):
        self.auth_calls.append((user, pwd))

    def post_login(self):
        self.post_login_called = True


class _FakeFrappe:
    class _Local:
        def __init__(self):
            self.login_manager = _FakeLoginManager()

    local = _Local()

    @staticmethod
    def throw(message: str, exc=None):
        error_type = exc or Exception
        raise error_type(message)


def test_login_with_personal_id_success(monkeypatch):
    fake_frappe = _FakeFrappe()
    monkeypatch.setattr(auth, "frappe", fake_frappe)
    monkeypatch.setattr(auth, "parse_args", lambda _args: {"personal_id": "11111"})

    result = auth.login_with_personal_id()

    assert result["status"] == "success"
    assert result["user"] == "marnisi.admin.north@example.com"
    assert fake_frappe.local.login_manager.auth_calls == [
        ("marnisi.admin.north@example.com", "Marnisi@2026#Seed!")
    ]
    assert fake_frappe.local.login_manager.post_login_called is True


def test_login_with_personal_id_unknown_id(monkeypatch):
    fake_frappe = _FakeFrappe()
    monkeypatch.setattr(auth, "frappe", fake_frappe)
    monkeypatch.setattr(auth, "parse_args", lambda _args: {"personal_id": "00000"})

    with pytest.raises(Exception, match="Unknown Personal ID"):
        auth.login_with_personal_id()
