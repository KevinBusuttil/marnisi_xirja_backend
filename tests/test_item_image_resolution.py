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


def test_legacy_price_edit_does_not_replace_image_with_placeholder():
    payload = {'sell_price': 49.152, 'image_path': 'assets/items/1.png', 'image_file': ''}
    safe = item._protect_legacy_image_update(payload)
    assert safe == {'sell_price': 49.152}
    assert payload['image_path'] == 'assets/items/1.png'


def test_explicit_placeholder_replacement_is_supported():
    payload = {'image_path': 'assets/items/1.png', 'replace_image': True}
    assert item._protect_legacy_image_update(payload) == payload


def test_real_replacement_clear_and_omission_are_preserved():
    for payload in ({'image_path': '/files/new.jpg'}, {'image_path': ''},
                    {'image_file': 'FILE-123'}, {'sell_price': 25}):
        assert item._protect_legacy_image_update(payload) == payload


def test_update_endpoint_preserves_image_columns_for_old_tablet(monkeypatch):
    from types import SimpleNamespace
    queries = []
    def sql(query, values=(), as_dict=False):
        queries.append((' '.join(query.split()), values))
        if 'SELECT name, vineyard, item_code' in query:
            return [{'name': 'wine', 'vineyard': "Marnisi M'Xlokk", 'item_code': 'WINE'}]
        return []
    monkeypatch.setattr(item.frappe, 'db', SimpleNamespace(sql=sql, commit=lambda: None))
    monkeypatch.setattr(item, 'parse_args', lambda _: {'item_id': 'wine', 'sell_price': 25,
        'image_path': 'assets/items/1.png', 'image_file': ''})
    monkeypatch.setattr(item, 'require_authenticated_user', lambda: 'cashier')
    monkeypatch.setattr(item, 'require_vineyard_permission', lambda *a, **kw: None)
    monkeypatch.setattr(item, '_has_image_file_column', lambda: True)
    monkeypatch.setattr(item.frappe.utils, 'now', lambda: '2026-11-02 12:00:00')
    result = item.update('{}')
    assert result['status'] == 'success'
    update, values = next((q, v) for q, v in queries if q.startswith('UPDATE'))
    assert 'sell_price = %s' in update and 25 in values
    assert 'image_path' not in update and 'image_file' not in update
