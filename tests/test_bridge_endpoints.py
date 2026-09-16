from __future__ import annotations

from xirja_marnisi.api import bridge


import pytest


@pytest.fixture(autouse=True)
def bridge_access(monkeypatch):
    monkeypatch.setattr(bridge, 'require_pos_access', lambda *a, **k: {'user': 'cashier@example.invalid'})
    monkeypatch.setattr(bridge, '_public_fiscal_context', lambda vineyard: {'mode':'Disabled'})


def test_get_all_users_scopes_personal_ids_to_authenticated_actor(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def sql(query, params, as_dict=False):
        calls.append((query, params))
        return [{'retail_personnel_id':'PERSONAL','retail_user_email':params[0]}]
    monkeypatch.setattr(bridge, 'frappe', SimpleNamespace(db=SimpleNamespace(sql=sql)))
    assert bridge.get_all_users()[0]['retail_user_email'] == 'cashier@example.invalid'
    assert calls[0][1] == ('cashier@example.invalid',)


def test_get_all_registers_respects_single_store_and_register_modes(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_resolve_vineyards",
        lambda: [
            {"vineyard": "VYD-NORTH"},
            {"vineyard": "VYD-SOUTH"},
            {"vineyard": bridge._LOCKED_STORE_ID},
        ],
    )

    result = bridge.get_all_registers()
    registers = result
    register_ids = {row["register_id"] for row in registers}

    expected_vineyards = {"VYD-NORTH", "VYD-SOUTH"}
    if bridge._SINGLE_STORE_MODE:
        expected_vineyards = {bridge._LOCKED_STORE_ID}

    for vineyard in expected_vineyards:
        assert f"{vineyard}-MAIN" in register_ids

    if bridge._SINGLE_REGISTER_MODE:
        assert not any(register_id.endswith("-TASTING") for register_id in register_ids)
    else:
        for vineyard in expected_vineyards:
            assert f"{vineyard}-TASTING" in register_ids


def test_get_all_stores_prefers_locked_store_when_available(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_resolve_vineyards",
        lambda: [
            {"vineyard": "VYD-NORTH"},
            {"vineyard": bridge._LOCKED_STORE_ID},
            {"vineyard": "VYD-SOUTH"},
        ],
    )

    stores = bridge.get_all_stores()
    store_ids = [row["store_id"] for row in stores]

    if bridge._SINGLE_STORE_MODE:
        assert store_ids == [bridge._LOCKED_STORE_ID]
    else:
        assert bridge._LOCKED_STORE_ID in store_ids


def test_get_all_stores_does_not_fabricate_locked_store_access(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_resolve_vineyards",
        lambda: [
            {"vineyard": "VYD-NORTH"},
            {"vineyard": "VYD-SOUTH"},
        ],
    )

    stores = bridge.get_all_stores()
    store_ids = [row["store_id"] for row in stores]

    if bridge._SINGLE_STORE_MODE:
        assert store_ids == []
    else:
        assert store_ids == ["VYD-NORTH", "VYD-SOUTH"]
