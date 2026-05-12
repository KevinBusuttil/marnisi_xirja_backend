from __future__ import annotations

from xirja_marnisi.api import seed


def test_get_tour_catalog_items_has_expected_tiers():
    items = seed._get_tour_catalog_items()
    item_names = {row["item_name"] for row in items}

    assert {"Tour Silver", "Tour Gold", "Tour Platinum"} == item_names


def test_get_tour_catalog_items_have_unique_codes_and_positive_prices():
    items = seed._get_tour_catalog_items()
    item_codes = [row["item_code"] for row in items]

    assert len(item_codes) == len(set(item_codes))
    assert all(float(row["price"]) > 0 for row in items)
