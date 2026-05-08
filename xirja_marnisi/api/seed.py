from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import frappe

from xirja_marnisi.api.security import parse_args, require_authenticated_user


_FALLBACK_WINES = [
    {
        "item_code": "FMW100001",
        "item_name": "100th Anniversary 0.75L",
        "category": "Maltese Wines",
        "brand": "Marsovin Estate/ Boutique Wines",
        "price": 43.01,
        "stock": 5,
    },
    {
        "item_code": "FMWANT012",
        "item_name": "Antonin Blanc 1.5L",
        "category": "Maltese Wines",
        "brand": "Marsovin DOK",
        "price": 35.21,
        "stock": 3,
    },
    {
        "item_code": "FMWANT013",
        "item_name": "Antonin Noir 1.5L",
        "category": "Maltese Wines",
        "brand": "Marsovin DOK",
        "price": 35.72,
        "stock": 2,
    },
    {
        "item_code": "FMWGRN020",
        "item_name": "Grand Maitre 2016 0.75L",
        "category": "Maltese Wines",
        "brand": "Marsovin Estate/ Boutique Wines",
        "price": 59.32,
        "stock": 1,
    },
    {
        "item_code": "FMWV18003",
        "item_name": "Valletta 2018 Wine Edition 3 - 0.75L",
        "category": "Maltese Wines",
        "brand": "Marsovin Estate/ Boutique Wines",
        "price": 11.86,
        "stock": 8,
    },
    {
        "item_code": "272/2017",
        "item_name": "Marnisi Organic 2017 0.75L",
        "category": "Maltese Wines",
        "brand": "Marsovin Estate/ Boutique Wines",
        "price": 25.42,
        "stock": 7,
    },
]


def _table_exists(table_name: str) -> bool:
    rows = frappe.db.sql("SHOW TABLES LIKE %s", (table_name,), as_dict=False)
    return bool(rows)


def _fetch_marsovin_maltese_items(limit: int = 120) -> list[dict[str, Any]]:
    if not _table_exists("tabRetail Items"):
        return []

    rows = frappe.db.sql(
        f"""
        SELECT
            item_id,
            item_name,
            item_category,
            item_brand,
            item_price,
            item_qty
        FROM `tabRetail Items`
        WHERE item_category = 'Maltese Wines'
          AND item_brand LIKE 'Marsovin%'
          AND IFNULL(item_price, 0) > 0
        ORDER BY item_name ASC
        LIMIT {int(limit)}
        """,
        as_dict=True,
    )

    mapped = []
    for row in rows:
        mapped.append(
            {
                "item_code": row.get("item_id"),
                "item_name": row.get("item_name"),
                "category": row.get("item_category") or "Maltese Wines",
                "brand": row.get("item_brand") or "Marsovin",
                "price": float(row.get("item_price") or 0),
                "stock": float(row.get("item_qty") or 0),
            }
        )

    return mapped


def _load_source_items_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    direct_items = payload.get("source_items")
    if isinstance(direct_items, list):
        return [row for row in direct_items if isinstance(row, dict)]

    source_items_path = str(payload.get("source_items_path") or "").strip()
    if source_items_path:
        file_path = Path(source_items_path)
        if file_path.exists():
            raw = file_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [row for row in parsed if isinstance(row, dict)]

    return []


def _ensure_role(role_name: str) -> None:
    if not role_name:
        return
    if frappe.db.exists("Role", role_name):
        return

    role = frappe.get_doc(
        {
            "doctype": "Role",
            "role_name": role_name,
            "desk_access": 1,
        }
    )
    role.insert(ignore_permissions=True)


def _ensure_user(
    email: str,
    full_name: str,
    roles: list[str] | None = None,
    password: str = "Marnisi@2026#Seed!",
) -> None:
    roles = roles or []
    for role_name in roles:
        _ensure_role(role_name)

    if frappe.db.exists("User", email):
        user = frappe.get_doc("User", email)
        if roles:
            try:
                user.add_roles(*roles)
            except Exception:
                pass
        return

    first, *rest = full_name.split(" ")
    last = " ".join(rest) if rest else "Admin"

    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": first,
            "last_name": last,
            "enabled": 1,
            "send_welcome_email": 0,
            "new_password": password,
        }
    )
    user.insert(ignore_permissions=True)
    if roles:
        try:
            user.add_roles(*roles)
        except Exception:
            pass


def _ensure_vineyard(vineyard_code: str, vineyard_name: str, timezone: str) -> str:
    existing = frappe.db.sql(
        "SELECT name FROM `tabVineyard` WHERE vineyard_code = %s LIMIT 1",
        (vineyard_code,),
        as_dict=True,
    )
    if existing:
        return existing[0]["name"]

    doc = frappe.get_doc(
        {
            "doctype": "Vineyard",
            "vineyard_code": vineyard_code,
            "vineyard_name": vineyard_name,
            "is_active": 1,
            "timezone": timezone,
        }
    )
    doc.insert(ignore_permissions=True)
    return doc.name


def _ensure_access(user: str, vineyard: str, access_role: str, is_default: int) -> None:
    exists = frappe.db.sql(
        """
        SELECT name
        FROM `tabVineyard User Access`
        WHERE `user` = %s
          AND vineyard = %s
        LIMIT 1
        """,
        (user, vineyard),
        as_dict=True,
    )

    if exists:
        frappe.db.sql(
            """
            UPDATE `tabVineyard User Access`
            SET access_role = %s,
                is_default = %s,
                is_active = 1,
                modified = %s,
                modified_by = %s
            WHERE name = %s
            """,
            (access_role, is_default, frappe.utils.now(), frappe.session.user, exists[0]["name"]),
        )
        return

    doc = frappe.get_doc(
        {
            "doctype": "Vineyard User Access",
            "user": user,
            "vineyard": vineyard,
            "access_role": access_role,
            "is_default": is_default,
            "is_active": 1,
        }
    )
    doc.insert(ignore_permissions=True)


def _seed_items_for_vineyard(vineyard: str, items: list[dict[str, Any]]) -> list[str]:
    created_items: list[str] = []
    image_index = 1

    for item in items:
        item_code = str(item.get("item_code") or "").strip()
        item_name = str(item.get("item_name") or "").strip()
        if not item_code or not item_name:
            continue

        item_key = f"{vineyard.lower()}::{item_code.lower()}"
        exists = frappe.db.sql(
            "SELECT name FROM `tabVineyard Item` WHERE item_key = %s LIMIT 1",
            (item_key,),
            as_dict=True,
        )
        if exists:
            created_items.append(exists[0]["name"])
            continue

        image_path = f"assets/items/{image_index}.png"
        image_index += 1
        if image_index > 12:
            image_index = 1

        doc = frappe.get_doc(
            {
                "doctype": "Vineyard Item",
                "vineyard": vineyard,
                "item_key": item_key,
                "item_code": item_code,
                "item_name": item_name,
                "category": item.get("category") or "Maltese Wines",
                "brand": item.get("brand") or "Marsovin",
                "image_path": image_path,
                "unit": "Bottle",
                "sell_price": float(item.get("price") or 0),
                "stock_qty": float(item.get("stock") or 0),
                "low_stock_threshold": 5,
                "is_enabled": 1,
            }
        )
        doc.insert(ignore_permissions=True)
        created_items.append(doc.name)

    return created_items


def _ensure_package(vineyard: str, package_tier: str, item_ids: list[str], qty: float) -> str:
    package_code = f"{package_tier.upper()}-{vineyard}"
    existing = frappe.db.sql(
        """
        SELECT name
        FROM `tabTour Package`
        WHERE vineyard = %s
          AND package_tier = %s
        LIMIT 1
        """,
        (vineyard, package_tier),
        as_dict=True,
    )

    if existing:
        doc = frappe.get_doc("Tour Package", existing[0]["name"])
    else:
        doc = frappe.new_doc("Tour Package")

    doc.vineyard = vineyard
    doc.package_code = package_code[:140]
    doc.package_name = package_tier.title()
    doc.package_tier = package_tier.title()
    doc.description = f"{package_tier.title()} tasting package"
    doc.price_per_person = {"Silver": 25.0, "Gold": 45.0, "Platinum": 75.0}.get(package_tier.title(), 30.0)
    doc.max_group_size = {"Silver": 12, "Gold": 18, "Platinum": 24}.get(package_tier.title(), 10)
    doc.is_active = 1

    doc.set("wines", [])
    for item_id in item_ids:
        doc.append(
            "wines",
            {
                "vineyard_item": item_id,
                "tasting_qty_per_guest": qty,
                "serving_uom": "Glass",
            },
        )

    if doc.name:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)

    return doc.name


@frappe.whitelist()
def seed_demo_data(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    require_authenticated_user()

    _ensure_role("Vineyard Admin")
    _ensure_role("Vineyard Staff")
    _ensure_role("Viewer")

    vineyards = [
        ("VYD-NORTH", "Marnisi Vineyard North", "Europe/Malta"),
        ("VYD-SOUTH", "Marnisi Vineyard South", "Europe/Malta"),
    ]

    _ensure_user(
        "marnisi.admin.north@example.com",
        "Marnisi North Admin",
        roles=["System Manager", "Vineyard Admin"],
    )
    _ensure_user(
        "marnisi.admin.south@example.com",
        "Marnisi South Admin",
        roles=["System Manager", "Vineyard Admin"],
    )
    _ensure_user("marnisi.staff@example.com", "Marnisi Staff", roles=["Vineyard Staff"])
    _ensure_user("marnisi.viewer@example.com", "Marnisi Viewer", roles=["Viewer"])

    item_source = _load_source_items_from_payload(payload)
    if not item_source:
        item_source = _fetch_marsovin_maltese_items(limit=60)
    if not item_source:
        item_source = _FALLBACK_WINES

    output = {
        "status": "success",
        "vineyards": [],
        "source_item_count": len(item_source),
    }

    for index, (code, name, timezone) in enumerate(vineyards):
        vineyard_id = _ensure_vineyard(code, name, timezone)

        _ensure_access(
            user="marnisi.admin.north@example.com" if index == 0 else "marnisi.admin.south@example.com",
            vineyard=vineyard_id,
            access_role="Vineyard Admin",
            is_default=1,
        )
        _ensure_access(
            user="marnisi.staff@example.com",
            vineyard=vineyard_id,
            access_role="Vineyard Staff",
            is_default=1 if index == 0 else 0,
        )
        _ensure_access(
            user="marnisi.viewer@example.com",
            vineyard=vineyard_id,
            access_role="Viewer",
            is_default=1 if index == 0 else 0,
        )

        vineyard_items = _seed_items_for_vineyard(vineyard_id, item_source[:18])

        silver_items = vineyard_items[:2]
        gold_items = vineyard_items[:3]
        platinum_items = vineyard_items[:5]

        silver_id = _ensure_package(vineyard_id, "Silver", silver_items, qty=1.0)
        gold_id = _ensure_package(vineyard_id, "Gold", gold_items, qty=1.5)
        platinum_id = _ensure_package(vineyard_id, "Platinum", platinum_items, qty=2.0)

        output["vineyards"].append(
            {
                "vineyard": vineyard_id,
                "items_seeded": len(vineyard_items),
                "packages": [silver_id, gold_id, platinum_id],
            }
        )

    frappe.db.commit()
    return output


@frappe.whitelist()
def export_marsovin_items(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    require_authenticated_user()
    limit = int(payload.get("limit") or 120)
    if limit < 1:
        limit = 120
    if limit > 2000:
        limit = 2000

    items = _fetch_marsovin_maltese_items(limit=limit)
    if not items:
        items = _FALLBACK_WINES

    return {
        "status": "success",
        "count": len(items),
        "items": items,
    }
