from __future__ import annotations

from types import SimpleNamespace
import json

import pytest

from xirja_marnisi.api import auth


@pytest.fixture
def login_env(monkeypatch):
    class LoginError(Exception):
        pass

    tracker = SimpleNamespace(failures=0)
    def fail_attempt():
        tracker.failures += 1
    tracker.add_failure_attempt = fail_attempt
    rows = [{"user": "operator@example.com"}]
    calls = []
    fake = SimpleNamespace(
        AuthenticationError=LoginError, form_dict={},
        session=SimpleNamespace(user="Guest"),
        local=SimpleNamespace(request_ip="127.0.0.1"),
    )
    def throw(message, exc=LoginError):
        raise exc(message)
    fake.throw = throw
    def sql(query, values, as_dict=False):
        assert values == ("P-001",)
        assert "m.is_active = 1" in query and "u.enabled = 1" in query
        return rows
    fake.db = SimpleNamespace(sql=sql)
    def login():
        calls.append(dict(fake.form_dict))
        if fake.form_dict["pwd"] != "individual-password":
            raise LoginError("Invalid login credentials")
        fake.session.user = fake.form_dict["usr"]
    fake.local.login_manager = SimpleNamespace(login=login)
    monkeypatch.setattr(auth, "frappe", fake)
    monkeypatch.setattr(auth, "_csrf_token", lambda: "csrf-for-session")
    monkeypatch.setattr("frappe.auth.get_login_attempt_tracker", lambda ip: tracker)
    return fake, rows, calls, tracker, LoginError


def test_login_uses_individual_password_and_full_frappe_flow(login_env):
    fake, _, calls, _, _ = login_env
    result = auth.login_with_personal_id({"personal_id": "P-001", "password": "individual-password"})
    assert result == {"status": "success", "user": "operator@example.com", "csrf_token": "csrf-for-session"}
    assert calls == [{"usr": "operator@example.com", "pwd": "individual-password"}]
    assert "pwd" not in fake.form_dict


def test_missing_password_never_authenticates(login_env):
    _, _, calls, tracker, exc = login_env
    with pytest.raises(exc, match="Invalid login credentials"):
        auth.login_with_personal_id({"personal_id": "P-001"})
    assert not calls and tracker.failures == 1


def test_wrong_password_never_returns_session(login_env):
    fake, _, _, _, exc = login_env
    with pytest.raises(exc):
        auth.login_with_personal_id({"personal_id": "P-001", "password": "wrong"})
    assert fake.session.user == "Guest"
    assert "pwd" not in fake.form_dict


@pytest.mark.parametrize("count", [0, 2])
def test_absent_disabled_or_ambiguous_mapping_is_rejected(login_env, count):
    _, rows, calls, tracker, exc = login_env
    rows[:] = [{"user": "operator@example.com"}] * count
    with pytest.raises(exc, match="Invalid login credentials"):
        auth.login_with_personal_id({"personal_id": "P-001", "password": "individual-password"})
    assert not calls and tracker.failures == 1


def test_incomplete_frappe_verification_is_rejected(login_env):
    fake, _, _, _, exc = login_env
    fake.local.login_manager.login = lambda: False
    with pytest.raises(exc, match="account verification"):
        auth.login_with_personal_id({"personal_id": "P-001", "password": "individual-password"})


@pytest.mark.parametrize("failure", ["malformed_json", "missing_id", "unmapped_id", "database", "throttled"])
def test_early_login_failure_removes_password_wrappers(login_env, monkeypatch, failure):
    fake, rows, calls, _, login_error = login_env
    raw = json.dumps({"personal_id": "P-001", "password": "individual-password"})
    expected_error = login_error
    if failure == "malformed_json":
        raw = raw[:-1]
    elif failure == "missing_id":
        raw = json.dumps({"password": "individual-password"})
    elif failure == "unmapped_id":
        rows.clear()
    elif failure in {"database", "throttled"}:
        def fail_before_login(*args, **kwargs):
            assert not {"args", "password", "pwd"}.intersection(fake.form_dict)
            raise RuntimeError("Pre-authentication failure")
        if failure == "database":
            fake.db.sql = fail_before_login
        else:
            monkeypatch.setattr("frappe.auth.get_login_attempt_tracker", fail_before_login)

    fake.form_dict.update({
        "args": raw,
        "password": "individual-password",
        "pwd": "individual-password",
    })
    with pytest.raises(expected_error):
        auth.login_with_personal_id(raw)
    assert not {"args", "password", "pwd"}.intersection(fake.form_dict)
    assert not calls
    assert fake.session.user == "Guest"


def test_unexpected_login_failure_has_only_a_sanitized_authentication_error(login_env):
    fake, _, calls, _, login_error = login_env

    def unavailable_mapping(*args, **kwargs):
        raise RuntimeError("private database detail and credential contents")

    fake.db.sql = unavailable_mapping
    with pytest.raises(login_error, match="Unable to complete POS login") as failure:
        auth.login_with_personal_id({"personal_id": "P-001", "password": "individual-password"})
    assert "private database" not in str(failure.value)
    assert "individual-password" not in str(failure.value)
    assert failure.value.__suppress_context__
    assert not calls
    assert fake.session.user == "Guest"
