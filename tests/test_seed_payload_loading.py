from __future__ import annotations

import json

from xirja_marnisi.api.seed import _load_source_items_from_payload


def test_load_source_items_from_payload_inline_list():
    payload = {
        "source_items": [
            {"item_code": "W1", "item_name": "Wine 1"},
            {"item_code": "W2", "item_name": "Wine 2"},
        ]
    }

    items = _load_source_items_from_payload(payload)

    assert len(items) == 2
    assert items[0]["item_code"] == "W1"


def test_load_source_items_from_payload_path(tmp_path):
    source_path = tmp_path / "items.json"
    source_path.write_text(
        json.dumps(
            [
                {"item_code": "W3", "item_name": "Wine 3"},
                {"item_code": "W4", "item_name": "Wine 4"},
            ]
        ),
        encoding="utf-8",
    )

    items = _load_source_items_from_payload({"source_items_path": str(source_path)})

    assert [row["item_code"] for row in items] == ["W3", "W4"]


def test_load_source_items_from_payload_ignores_non_dict_rows():
    payload = {
        "source_items": [
            {"item_code": "W1", "item_name": "Wine 1"},
            "invalid",
            123,
            {"item_code": "W2", "item_name": "Wine 2"},
        ]
    }

    items = _load_source_items_from_payload(payload)

    assert [row["item_code"] for row in items] == ["W1", "W2"]


def test_load_source_items_from_payload_missing_file_returns_empty(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"

    items = _load_source_items_from_payload({"source_items_path": str(missing_path)})

    assert items == []
