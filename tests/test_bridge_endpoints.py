from __future__ import annotations

from xirja_marnisi.api import bridge


def test_get_all_users_contains_seed_personal_ids():
    result = bridge.get_all_users()
    users = result
    ids = {row["retail_personnel_id"] for row in users}
    assert {"11111", "22222", "33333", "44444"}.issubset(ids)


def test_get_all_registers_creates_main_and_tasting(monkeypatch):
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

    assert "VYD-NORTH-MAIN" in register_ids
    assert "VYD-NORTH-TASTING" in register_ids
    assert "VYD-SOUTH-MAIN" in register_ids
    assert "VYD-SOUTH-TASTING" in register_ids
