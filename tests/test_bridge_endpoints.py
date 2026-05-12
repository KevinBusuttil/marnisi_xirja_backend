from __future__ import annotations

from xirja_marnisi.api import bridge


def test_get_all_users_contains_seed_personal_ids():
    result = bridge.get_all_users()
    users = result
    ids = {row["retail_personnel_id"] for row in users}
    assert {"11111", "22222", "33333", "44444"}.issubset(ids)


def test_get_all_registers_respects_single_store_and_register_modes(monkeypatch):
    monkeypatch.setattr(
        bridge,
        "_resolve_vineyards",
        lambda: [
            {"vineyard": "VYD-NORTH"},
            {"vineyard": "VYD-SOUTH"},
        ],
    )

    result = bridge.get_all_registers()
    registers = result
    register_ids = {row["register_id"] for row in registers}

    expected_vineyards = {"VYD-NORTH", "VYD-SOUTH"}
    if bridge._SINGLE_STORE_MODE:
        expected_vineyards = {"VYD-NORTH"}

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
