from __future__ import annotations

from typing import Any

import frappe

from xirja_marnisi.api.security import (
    parse_args,
    require_authenticated_user,
    require_vineyard_permission,
    resolve_vineyard,
    to_bool,
)


@frappe.whitelist()
def list(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()
    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=False)

    rows = frappe.db.sql(
        """
        SELECT
            tp.name,
            tp.vineyard,
            tp.package_code,
            tp.package_name,
            tp.package_tier,
            IFNULL(tp.price_per_person, 0) AS price_per_person,
            IFNULL(tp.max_group_size, 0) AS max_group_size,
            IFNULL(tp.is_active, 1) AS is_active,
            tp.description,
            COUNT(tpw.name) AS wine_count,
            tp.modified
        FROM `tabTour Package` tp
        LEFT JOIN `tabTour Package Wine` tpw
            ON tpw.parent = tp.name
        WHERE tp.vineyard = %s
        GROUP BY tp.name
        ORDER BY tp.package_tier ASC, tp.package_name ASC
        """,
        (vineyard,),
        as_dict=True,
    )

    wines_by_package: dict[str, list[dict[str, Any]]] = {}
    if rows:
        package_names = [row["name"] for row in rows]
        wines = frappe.db.sql(
            """
            SELECT
                name,
                parent,
                vineyard_item,
                tasting_qty_per_guest,
                serving_uom
            FROM `tabTour Package Wine`
            WHERE parent IN %(package_names)s
            ORDER BY parent ASC, idx ASC
            """,
            {"package_names": tuple(package_names)},
            as_dict=True,
        )
        for wine in wines:
            parent = wine.get("parent")
            if not parent:
                continue
            wines_by_package.setdefault(parent, []).append(
                {
                    "name": wine.get("name"),
                    "vineyard_item": wine.get("vineyard_item"),
                    "tasting_qty_per_guest": wine.get("tasting_qty_per_guest"),
                    "serving_uom": wine.get("serving_uom"),
                }
            )

    packages: list[dict[str, Any]] = [{**row, "wines": wines_by_package.get(row["name"], [])} for row in rows]

    return {
        "status": "success",
        "vineyard": vineyard,
        "packages": packages,
        "count": len(packages),
    }


@frappe.whitelist()
def upsert(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()

    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=True)

    package_name = str(payload.get("package_name") or "").strip()
    package_tier = str(payload.get("package_tier") or "").strip() or "Custom"
    package_code = str(payload.get("package_code") or "").strip()
    package_id = str(payload.get("package_id") or payload.get("name") or "").strip()

    if not package_name:
        frappe.throw("package_name is required")

    wines = payload.get("wines") or []
    if not isinstance(wines, list) or not wines:
        frappe.throw("At least one wine is required in a package")

    validated_wines: list[dict[str, Any]] = []
    for row in wines:
        vineyard_item = str((row or {}).get("vineyard_item") or "").strip()
        if not vineyard_item:
            frappe.throw("Each package wine requires vineyard_item")

        item_exists = frappe.db.sql(
            """
            SELECT name
            FROM `tabVineyard Item`
            WHERE name = %s
              AND vineyard = %s
              AND IFNULL(is_enabled, 1) = 1
            LIMIT 1
            """,
            (vineyard_item, vineyard),
            as_dict=True,
        )
        if not item_exists:
            frappe.throw(f"Package wine {vineyard_item} is not available in this vineyard")

        validated_wines.append(
            {
                "vineyard_item": vineyard_item,
                "tasting_qty_per_guest": float((row or {}).get("tasting_qty_per_guest") or 1),
                "serving_uom": str((row or {}).get("serving_uom") or "Glass").strip(),
            }
        )

    if package_id:
        doc = frappe.get_doc("Tour Package", package_id)
        if doc.vineyard != vineyard:
            frappe.throw("Cannot move package across vineyards")
    else:
        if not package_code:
            normalized_tier = package_tier.upper().replace(" ", "_")
            package_code = f"{normalized_tier}-{frappe.generate_hash(length=5).upper()}"
        doc = frappe.new_doc("Tour Package")

    doc.vineyard = vineyard
    doc.package_code = package_code
    doc.package_name = package_name
    doc.package_tier = package_tier
    doc.price_per_person = float(payload.get("price_per_person") or 0)
    doc.max_group_size = int(payload.get("max_group_size") or 0)
    doc.is_active = 1 if to_bool(payload.get("is_active", True)) else 0
    doc.description = str(payload.get("description") or "").strip()

    doc.set("wines", [])
    for wine in validated_wines:
        doc.append("wines", wine)

    if package_id:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)

    frappe.db.commit()

    return {
        "status": "success",
        "package": {
            "name": doc.name,
            "vineyard": doc.vineyard,
            "package_code": doc.package_code,
            "package_name": doc.package_name,
            "package_tier": doc.package_tier,
            "wine_count": len(validated_wines),
        },
    }
