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


def _item_key(vineyard: str, item_code: str) -> str:
    return f"{vineyard.strip().lower()}::{item_code.strip().lower()}"


def _insert_stock_movement(
    vineyard: str,
    vineyard_item: str,
    movement_type: str,
    qty_before: float,
    qty_delta: float,
    qty_after: float,
    actor_user: str,
    reason: str = "",
    tour_booking: str = "",
) -> None:
    ts = frappe.utils.now()
    name = f"SM-{frappe.generate_hash(length=12)}"
    frappe.db.sql(
        """
        INSERT INTO `tabStock Movement` (
            name, creation, modified, modified_by, owner, docstatus, idx,
            vineyard, vineyard_item, tour_booking, movement_type,
            qty_before, qty_delta, qty_after, reason, actor_user
        ) VALUES (
            %s, %s, %s, %s, %s, 0, 0,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        """,
        (
            name,
            ts,
            ts,
            actor_user,
            actor_user,
            vineyard,
            vineyard_item,
            tour_booking or None,
            movement_type,
            float(qty_before),
            float(qty_delta),
            float(qty_after),
            reason or "",
            actor_user,
        ),
    )


@frappe.whitelist()
def list(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()
    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=False)

    conditions = ["vineyard = %s"]
    values: list[Any] = [vineyard]

    if "enabled" in payload and str(payload.get("enabled")).strip() != "":
        conditions.append("IFNULL(is_enabled, 1) = %s")
        values.append(1 if to_bool(payload.get("enabled")) else 0)

    search = str(payload.get("search") or "").strip().lower()
    if search:
        conditions.append(
            "(" + " OR ".join(
                [
                    "LOWER(item_code) LIKE %s",
                    "LOWER(item_name) LIKE %s",
                    "LOWER(category) LIKE %s",
                    "LOWER(brand) LIKE %s",
                ]
            ) + ")"
        )
        token = f"%{search}%"
        values.extend([token, token, token, token])

    if to_bool(payload.get("low_stock")):
        conditions.append("IFNULL(stock_qty, 0) <= IFNULL(low_stock_threshold, 0)")

    where_clause = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            vineyard,
            item_code,
            item_name,
            category,
            brand,
            image_path,
            unit,
            IFNULL(sell_price, 0) AS sell_price,
            IFNULL(stock_qty, 0) AS stock_qty,
            IFNULL(low_stock_threshold, 0) AS low_stock_threshold,
            IFNULL(is_enabled, 1) AS is_enabled,
            notes,
            modified
        FROM `tabVineyard Item`
        WHERE {where_clause}
        ORDER BY item_name ASC
        """,
        tuple(values),
        as_dict=True,
    )

    return {
        "status": "success",
        "vineyard": vineyard,
        "items": rows,
        "count": len(rows),
    }


@frappe.whitelist()
def create(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()
    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=True)

    item_code = str(payload.get("item_code") or "").strip()
    item_name = str(payload.get("item_name") or "").strip()

    if not item_code or not item_name:
        frappe.throw("item_code and item_name are required")

    key = _item_key(vineyard, item_code)
    exists = frappe.db.sql(
        "SELECT name FROM `tabVineyard Item` WHERE item_key = %s LIMIT 1",
        (key,),
        as_dict=True,
    )
    if exists:
        frappe.throw("Item already exists for this vineyard")

    initial_stock = float(payload.get("stock_qty") or 0)
    item_doc = frappe.get_doc(
        {
            "doctype": "Vineyard Item",
            "vineyard": vineyard,
            "item_key": key,
            "item_code": item_code,
            "item_name": item_name,
            "category": str(payload.get("category") or "").strip(),
            "brand": str(payload.get("brand") or "").strip(),
            "image_path": str(payload.get("image_path") or "assets/items/1.png").strip(),
            "unit": str(payload.get("unit") or "Bottle").strip(),
            "sell_price": float(payload.get("sell_price") or 0),
            "stock_qty": initial_stock,
            "low_stock_threshold": float(payload.get("low_stock_threshold") or 0),
            "is_enabled": 1 if to_bool(payload.get("is_enabled", True)) else 0,
            "notes": str(payload.get("notes") or "").strip(),
        }
    )
    item_doc.insert(ignore_permissions=True)

    _insert_stock_movement(
        vineyard=vineyard,
        vineyard_item=item_doc.name,
        movement_type="ADJUST_SET",
        qty_before=0,
        qty_delta=initial_stock,
        qty_after=initial_stock,
        reason="Initial item creation",
        actor_user=user,
    )

    frappe.db.commit()

    return {
        "status": "success",
        "item": {
            "name": item_doc.name,
            "vineyard": vineyard,
            "item_code": item_doc.item_code,
            "item_name": item_doc.item_name,
        },
    }


@frappe.whitelist()
def update(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()

    item_name = str(payload.get("item_id") or payload.get("name") or "").strip()
    if not item_name:
        frappe.throw("item_id is required")

    item_row = frappe.db.sql(
        """
        SELECT name, vineyard, item_code
        FROM `tabVineyard Item`
        WHERE name = %s
        LIMIT 1
        """,
        (item_name,),
        as_dict=True,
    )
    if not item_row:
        frappe.throw("Item not found")

    item_row = item_row[0]
    vineyard = item_row["vineyard"]
    require_vineyard_permission(vineyard, mutate=True)

    updates: dict[str, Any] = {}
    allowed_fields = [
        "item_name",
        "category",
        "brand",
        "image_path",
        "unit",
        "sell_price",
        "low_stock_threshold",
        "notes",
    ]

    for field in allowed_fields:
        if field in payload:
            updates[field] = payload.get(field)

    if "item_code" in payload:
        new_code = str(payload.get("item_code") or "").strip()
        if not new_code:
            frappe.throw("item_code cannot be blank")
        updates["item_code"] = new_code
        updates["item_key"] = _item_key(vineyard, new_code)

    if not updates:
        return {"status": "success", "message": "Nothing to update"}

    updates["modified"] = frappe.utils.now()
    updates["modified_by"] = user

    set_clause = ", ".join([f"{field} = %s" for field in updates])
    values = list(updates.values()) + [item_name]

    frappe.db.sql(
        f"UPDATE `tabVineyard Item` SET {set_clause} WHERE name = %s",
        tuple(values),
    )
    frappe.db.commit()

    return {
        "status": "success",
        "item_id": item_name,
    }


@frappe.whitelist()
def set_enabled(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()

    item_name = str(payload.get("item_id") or payload.get("name") or "").strip()
    enabled = 1 if to_bool(payload.get("enabled")) else 0

    row = frappe.db.sql(
        "SELECT name, vineyard, stock_qty, IFNULL(is_enabled, 1) AS is_enabled FROM `tabVineyard Item` WHERE name = %s LIMIT 1",
        (item_name,),
        as_dict=True,
    )
    if not row:
        frappe.throw("Item not found")

    row = row[0]
    vineyard = row["vineyard"]
    require_vineyard_permission(vineyard, mutate=True)

    frappe.db.sql(
        """
        UPDATE `tabVineyard Item`
        SET is_enabled = %s,
            modified = %s,
            modified_by = %s
        WHERE name = %s
        """,
        (enabled, frappe.utils.now(), user, item_name),
    )

    _insert_stock_movement(
        vineyard=vineyard,
        vineyard_item=item_name,
        movement_type="ENABLE_DISABLE",
        qty_before=float(row.get("stock_qty") or 0),
        qty_delta=0,
        qty_after=float(row.get("stock_qty") or 0),
        reason="Enabled" if enabled else "Disabled",
        actor_user=user,
    )

    frappe.db.commit()

    return {
        "status": "success",
        "item_id": item_name,
        "enabled": bool(enabled),
    }


@frappe.whitelist()
def adjust_stock(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()

    item_name = str(payload.get("item_id") or payload.get("name") or "").strip()
    mode = str(payload.get("mode") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()

    if mode not in {"set", "delta"}:
        frappe.throw("mode must be 'set' or 'delta'")

    try:
        set_qty = float(payload.get("set_qty")) if payload.get("set_qty") is not None else None
        delta_qty = float(payload.get("delta_qty")) if payload.get("delta_qty") is not None else None
    except Exception:
        frappe.throw("Invalid quantity value")

    if mode == "set" and set_qty is None:
        frappe.throw("set_qty is required for set mode")
    if mode == "delta" and delta_qty is None:
        frappe.throw("delta_qty is required for delta mode")

    try:
        frappe.db.sql("START TRANSACTION")
        row = frappe.db.sql(
            """
            SELECT
                name,
                vineyard,
                IFNULL(stock_qty, 0) AS stock_qty
            FROM `tabVineyard Item`
            WHERE name = %s
            FOR UPDATE
            """,
            (item_name,),
            as_dict=True,
        )

        if not row:
            frappe.throw("Item not found")

        row = row[0]
        vineyard = row["vineyard"]
        require_vineyard_permission(vineyard, mutate=True)

        before_qty = float(row.get("stock_qty") or 0)
        if mode == "set":
            after_qty = float(set_qty or 0)
            delta = after_qty - before_qty
            movement_type = "ADJUST_SET"
        else:
            delta = float(delta_qty or 0)
            after_qty = before_qty + delta
            movement_type = "ADJUST_DELTA"

        if after_qty < 0:
            frappe.throw("Stock cannot be negative")

        frappe.db.sql(
            """
            UPDATE `tabVineyard Item`
            SET stock_qty = %s,
                modified = %s,
                modified_by = %s
            WHERE name = %s
            """,
            (after_qty, frappe.utils.now(), user, item_name),
        )

        _insert_stock_movement(
            vineyard=vineyard,
            vineyard_item=item_name,
            movement_type=movement_type,
            qty_before=before_qty,
            qty_delta=delta,
            qty_after=after_qty,
            reason=reason,
            actor_user=user,
        )

        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise

    return {
        "status": "success",
        "item_id": item_name,
        "stock_qty": after_qty,
    }


@frappe.whitelist()
def list_movements(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()
    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=False)

    item_id = str(payload.get("item_id") or "").strip()
    limit = int(payload.get("limit") or 100)
    if limit < 1:
        limit = 50
    if limit > 500:
        limit = 500

    conditions = ["vineyard = %s"]
    values: list[Any] = [vineyard]

    if item_id:
        conditions.append("vineyard_item = %s")
        values.append(item_id)

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            creation,
            vineyard,
            vineyard_item,
            tour_booking,
            movement_type,
            qty_before,
            qty_delta,
            qty_after,
            reason,
            actor_user
        FROM `tabStock Movement`
        WHERE {' AND '.join(conditions)}
        ORDER BY creation DESC
        LIMIT {limit}
        """,
        tuple(values),
        as_dict=True,
    )

    return {
        "status": "success",
        "movements": rows,
        "count": len(rows),
    }
