from __future__ import annotations

from xirja_marnisi.api import bridge


class _FakeDB:
    @staticmethod
    def sql(query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        if "FROM `tabVineyard Item`" in normalized:
            rows = [
                {
                    "name": "ITEM-N-1",
                    "vineyard": "VYD-NORTH",
                    "item_code": "FMW100001",
                    "item_name": "North Wine",
                    "category": "Maltese Wines",
                    "brand": "Marsovin",
                    "image_path": "assets/items/1.png",
                    "unit": "Bottle",
                    "sell_price": 10,
                    "stock_qty": 5,
                },
                {
                    "name": "ITEM-S-1",
                    "vineyard": "VYD-SOUTH",
                    "item_code": "FMW100001",
                    "item_name": "South Wine",
                    "category": "Maltese Wines",
                    "brand": "Marsovin",
                    "image_path": "assets/items/2.png",
                    "unit": "Bottle",
                    "sell_price": 11,
                    "stock_qty": 6,
                },
                {
                    "name": "ITEM-M-1",
                    "vineyard": bridge._LOCKED_STORE_ID,
                    "item_code": "TOUR-GOLD",
                    "item_name": "Tour Gold",
                    "category": "Tour",
                    "brand": "Marsovin",
                    "image_path": "assets/items/3.png",
                    "unit": "Bottle",
                    "sell_price": 20,
                    "stock_qty": 10,
                },
            ]
            if params:
                vineyard_filter = str(params[0] or "")
                return [
                    row for row in rows if str(row.get("vineyard") or "") == vineyard_filter
                ]
            return rows
        return []


class _FakeFrappe:
    db = _FakeDB()


def test_get_all_products_returns_vineyard_scoped_item_ids(monkeypatch):
    monkeypatch.setattr(bridge, "frappe", _FakeFrappe())

    result = bridge.get_all_products()
    ids = {row["item_id"] for row in result}
    stores = {row["item_store"] for row in result}

    assert f"{bridge._LOCKED_STORE_ID}::TOUR-GOLD" in ids
    if bridge._SINGLE_STORE_MODE:
        assert "VYD-NORTH::FMW100001" not in ids
        assert "VYD-SOUTH::FMW100001" not in ids
        assert len(ids) == 1
        assert stores == {bridge._LOCKED_STORE_ID}
    else:
        assert "VYD-NORTH::FMW100001" in ids
        assert "VYD-SOUTH::FMW100001" in ids
        assert len(ids) == 3
        assert stores == {"VYD-NORTH", "VYD-SOUTH", bridge._LOCKED_STORE_ID}


class _FakeDBImageFile:
    @staticmethod
    def sql(query, params=None, as_dict=False):
        normalized = " ".join(str(query).split())
        if "FROM `tabVineyard Item`" in normalized:
            rows = [
                {
                    "name": "ITEM-N-2",
                    "vineyard": bridge._LOCKED_STORE_ID,
                    "item_code": "FMW100002",
                    "item_name": "North Wine Alt",
                    "category": "Maltese Wines",
                    "brand": "Marsovin",
                    "image_path": "",
                    "image_file_url": "/files/north-alt.jpg",
                    "unit": "Bottle",
                    "sell_price": 15,
                    "stock_qty": 8,
                }
            ]
            if params:
                vineyard_filter = str(params[0] or "")
                return [
                    row for row in rows if str(row.get("vineyard") or "") == vineyard_filter
                ]
            return rows
        return []


class _FakeFrappeImageFile:
    db = _FakeDBImageFile()


def test_get_all_products_falls_back_to_image_file_url_when_image_path_empty(monkeypatch):
    monkeypatch.setattr(bridge, "frappe", _FakeFrappeImageFile())
    monkeypatch.setattr(bridge, "_column_exists", lambda _table, _column: True)

    result = bridge.get_all_products()

    assert len(result) == 1
    assert result[0]["item_id"] == f"{bridge._LOCKED_STORE_ID}::FMW100002"
    assert result[0]["item_img_path"] == "/files/north-alt.jpg"
    if bridge._SINGLE_STORE_MODE:
        assert result[0]["item_store"] == bridge._LOCKED_STORE_ID
    else:
        assert result[0]["item_store"] == bridge._LOCKED_STORE_ID
