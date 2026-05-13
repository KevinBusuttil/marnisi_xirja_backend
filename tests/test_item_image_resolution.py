from __future__ import annotations

from xirja_marnisi.api import item


def test_resolve_payload_image_fields_prefers_explicit_image_path(monkeypatch):
    monkeypatch.setattr(item, "_has_image_file_column", lambda: True)
    monkeypatch.setattr(item, "_file_name_from_url", lambda _url: "FILE-0001")

    path, image_file = item._resolve_payload_image_fields(  # noqa: SLF001
        {
            "image_path": "/files/marnisi-item.jpg",
            "image_file": "",
        },
        fallback_to_default=False,
    )

    assert path == "/files/marnisi-item.jpg"
    assert image_file == "FILE-0001"


def test_resolve_payload_image_fields_uses_image_file_dropdown(monkeypatch):
    monkeypatch.setattr(item, "_has_image_file_column", lambda: True)
    monkeypatch.setattr(item, "_file_url_from_name", lambda _name: "/files/from-dropdown.jpg")

    path, image_file = item._resolve_payload_image_fields(  # noqa: SLF001
        {
            "image_path": "",
            "image_file": "FILE-0002",
        },
        fallback_to_default=False,
    )

    assert path == "/files/from-dropdown.jpg"
    assert image_file == "FILE-0002"


def test_resolve_payload_image_fields_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(item, "_has_image_file_column", lambda: True)

    path, image_file = item._resolve_payload_image_fields(  # noqa: SLF001
        {
            "image_path": "",
            "image_file": "",
        },
        fallback_to_default=True,
    )

    assert path == "assets/items/1.png"
    assert image_file == ""


def test_effective_image_path_uses_file_url_when_primary_missing():
    assert item._effective_image_path("", "/files/item-url.jpg") == "/files/item-url.jpg"  # noqa: SLF001
    assert item._effective_image_path("/files/item-main.jpg", "/files/other.jpg") == "/files/item-main.jpg"  # noqa: SLF001
    assert item._effective_image_path("", "") == "assets/items/1.png"  # noqa: SLF001
