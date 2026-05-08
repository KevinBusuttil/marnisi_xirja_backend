from __future__ import annotations

from typing import Any

import frappe

from xirja_marnisi.api.item import _insert_stock_movement
from xirja_marnisi.api.security import parse_args, require_authenticated_user, require_vineyard_permission, resolve_vineyard

_TRANSITIONS = {
    "DRAFT": {"CONFIRMED", "CANCELLED"},
    "CONFIRMED": {"CHECKED_IN", "CANCELLED"},
    "CHECKED_IN": {"COMPLETED", "CANCELLED"},
    "COMPLETED": set(),
    "CANCELLED": set(),
}
_CREATE_ALLOWED_STATUSES = {"DRAFT", "CONFIRMED"}
_TOUR_TYPES = {"INDIVIDUAL", "GROUP"}


def _get_package_wines(package_id: str) -> list[dict[str, Any]]:
    return frappe.db.sql(
        """
        SELECT
            vineyard_item,
            IFNULL(tasting_qty_per_guest, 0) AS tasting_qty_per_guest,
            IFNULL(serving_uom, 'Glass') AS serving_uom
        FROM `tabTour Package Wine`
        WHERE parent = %s
        ORDER BY idx ASC
        """,
        (package_id,),
        as_dict=True,
    )


def _deduct_or_restore_stock(
    *,
    vineyard: str,
    booking_id: str,
    package_id: str,
    participants_count: int,
    actor_user: str,
    movement_type: str,
    restore: bool = False,
) -> None:
    wines = _get_package_wines(package_id)
    if not wines:
        frappe.throw("Package has no wines configured")

    for wine in wines:
        vineyard_item = wine["vineyard_item"]
        per_guest = float(wine.get("tasting_qty_per_guest") or 0)
        qty = per_guest * max(participants_count, 0)

        item_row = frappe.db.sql(
            """
            SELECT
                name,
                IFNULL(stock_qty, 0) AS stock_qty
            FROM `tabVineyard Item`
            WHERE name = %s
              AND vineyard = %s
            FOR UPDATE
            """,
            (vineyard_item, vineyard),
            as_dict=True,
        )
        if not item_row:
            frappe.throw(f"Vineyard item not found for package line: {vineyard_item}")

        item_row = item_row[0]
        before_qty = float(item_row.get("stock_qty") or 0)

        if restore:
            after_qty = before_qty + qty
            delta = qty
        else:
            after_qty = before_qty - qty
            delta = -qty
            if after_qty < 0:
                frappe.throw(
                    f"Insufficient stock for item {vineyard_item}. Available={before_qty}, required={qty}"
                )

        frappe.db.sql(
            """
            UPDATE `tabVineyard Item`
            SET stock_qty = %s,
                modified = %s,
                modified_by = %s
            WHERE name = %s
            """,
            (after_qty, frappe.utils.now(), actor_user, vineyard_item),
        )

        _insert_stock_movement(
            vineyard=vineyard,
            vineyard_item=vineyard_item,
            movement_type=movement_type,
            qty_before=before_qty,
            qty_delta=delta,
            qty_after=after_qty,
            reason="Tour booking stock movement",
            actor_user=actor_user,
            tour_booking=booking_id,
        )


@frappe.whitelist()
def list(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()
    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=False)

    conditions = ["vineyard = %s"]
    values: list[Any] = [vineyard]

    status = str(payload.get("status") or "").strip().upper()
    if status:
        conditions.append("status = %s")
        values.append(status)

    from_date = str(payload.get("from_date") or "").strip()
    to_date = str(payload.get("to_date") or "").strip()
    if from_date and to_date:
        conditions.append("DATE(scheduled_at) BETWEEN %s AND %s")
        values.extend([from_date, to_date])
    elif from_date:
        conditions.append("DATE(scheduled_at) >= %s")
        values.append(from_date)
    elif to_date:
        conditions.append("DATE(scheduled_at) <= %s")
        values.append(to_date)

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            booking_no,
            vineyard,
            tour_package,
            tour_type,
            participants_count,
            scheduled_at,
            guest_name,
            guest_phone,
            guest_email,
            status,
            IFNULL(stock_deducted, 0) AS stock_deducted,
            checkin_at,
            completed_at,
            cancel_reason,
            notes,
            modified
        FROM `tabTour Booking`
        WHERE {' AND '.join(conditions)}
        ORDER BY scheduled_at DESC, modified DESC
        """,
        tuple(values),
        as_dict=True,
    )

    return {
        "status": "success",
        "vineyard": vineyard,
        "bookings": rows,
        "count": len(rows),
    }


@frappe.whitelist()
def create(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()
    vineyard = resolve_vineyard(payload, user)
    require_vineyard_permission(vineyard, mutate=True, staff_allowed=True)

    package_id = str(payload.get("tour_package") or "").strip()
    if not package_id:
        frappe.throw("tour_package is required")

    package_row = frappe.db.sql(
        """
        SELECT name, vineyard, IFNULL(is_active, 1) AS is_active
        FROM `tabTour Package`
        WHERE name = %s
        LIMIT 1
        """,
        (package_id,),
        as_dict=True,
    )
    if not package_row:
        frappe.throw("Tour package not found")

    package_row = package_row[0]
    if package_row["vineyard"] != vineyard:
        frappe.throw("Tour package does not belong to this vineyard")
    if int(package_row.get("is_active") or 0) != 1:
        frappe.throw("Tour package is disabled")

    try:
        participants = int(payload.get("participants_count") or 1)
    except Exception:
        frappe.throw("participants_count must be a valid integer")
    if participants <= 0:
        frappe.throw("participants_count must be positive")

    tour_type = str(payload.get("tour_type") or "INDIVIDUAL").strip().upper()
    if tour_type not in _TOUR_TYPES:
        frappe.throw("tour_type must be INDIVIDUAL or GROUP")

    create_status = str(payload.get("status") or "DRAFT").strip().upper()
    if create_status not in _CREATE_ALLOWED_STATUSES:
        frappe.throw("status must be DRAFT or CONFIRMED when creating a booking")

    booking_no = str(payload.get("booking_no") or "").strip() or f"TBK-{frappe.generate_hash(length=8).upper()}"

    doc = frappe.get_doc(
        {
            "doctype": "Tour Booking",
            "booking_no": booking_no,
            "vineyard": vineyard,
            "tour_package": package_id,
            "tour_type": tour_type,
            "participants_count": participants,
            "scheduled_at": payload.get("scheduled_at") or frappe.utils.now_datetime(),
            "guest_name": str(payload.get("guest_name") or "").strip(),
            "guest_phone": str(payload.get("guest_phone") or "").strip(),
            "guest_email": str(payload.get("guest_email") or "").strip(),
            "status": create_status,
            "stock_deducted": 0,
            "notes": str(payload.get("notes") or "").strip(),
        }
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    return {
        "status": "success",
        "booking": {
            "name": doc.name,
            "booking_no": doc.booking_no,
            "vineyard": doc.vineyard,
            "tour_package": doc.tour_package,
            "status": doc.status,
        },
    }


@frappe.whitelist()
def update_status(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()

    booking_id = str(payload.get("booking_id") or payload.get("name") or "").strip()
    target_status = str(payload.get("status") or "").strip().upper()
    if not booking_id or not target_status:
        frappe.throw("booking_id and status are required")

    try:
        frappe.db.sql("START TRANSACTION")
        booking_rows = frappe.db.sql(
            """
            SELECT
                name,
                vineyard,
                tour_package,
                IFNULL(participants_count, 0) AS participants_count,
                status,
                IFNULL(stock_deducted, 0) AS stock_deducted
            FROM `tabTour Booking`
            WHERE name = %s
            FOR UPDATE
            """,
            (booking_id,),
            as_dict=True,
        )
        if not booking_rows:
            frappe.throw("Booking not found")

        booking = booking_rows[0]
        vineyard = booking["vineyard"]
        require_vineyard_permission(vineyard, mutate=True, staff_allowed=True)

        current_status = str(booking.get("status") or "DRAFT").upper()
        allowed_next = _TRANSITIONS.get(current_status, set())
        if target_status not in allowed_next:
            frappe.throw(f"Invalid transition: {current_status} -> {target_status}")

        stock_deducted = int(booking.get("stock_deducted") or 0)
        participants_count = int(booking.get("participants_count") or 0)
        package_id = booking["tour_package"]

        if target_status == "CHECKED_IN" and stock_deducted == 0:
            _deduct_or_restore_stock(
                vineyard=vineyard,
                booking_id=booking_id,
                package_id=package_id,
                participants_count=participants_count,
                actor_user=user,
                movement_type="AUTO_DEDUCT",
                restore=False,
            )
            stock_deducted = 1

        if target_status == "CANCELLED" and current_status == "CHECKED_IN" and stock_deducted == 1:
            _deduct_or_restore_stock(
                vineyard=vineyard,
                booking_id=booking_id,
                package_id=package_id,
                participants_count=participants_count,
                actor_user=user,
                movement_type="AUTO_RESTORE",
                restore=True,
            )
            stock_deducted = 0

        ts = frappe.utils.now()
        checkin_at = ts if target_status == "CHECKED_IN" else None
        completed_at = ts if target_status == "COMPLETED" else None

        frappe.db.sql(
            """
            UPDATE `tabTour Booking`
            SET status = %s,
                stock_deducted = %s,
                checkin_at = CASE WHEN %s IS NOT NULL THEN %s ELSE checkin_at END,
                completed_at = CASE WHEN %s IS NOT NULL THEN %s ELSE completed_at END,
                cancel_reason = CASE WHEN %s = 'CANCELLED' THEN %s ELSE cancel_reason END,
                modified = %s,
                modified_by = %s
            WHERE name = %s
            """,
            (
                target_status,
                stock_deducted,
                checkin_at,
                checkin_at,
                completed_at,
                completed_at,
                target_status,
                str(payload.get("cancel_reason") or "").strip(),
                ts,
                user,
                booking_id,
            ),
        )

        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise

    return {
        "status": "success",
        "booking_id": booking_id,
        "status_value": target_status,
        "stock_deducted": stock_deducted,
    }


@frappe.whitelist()
def get(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    user = require_authenticated_user()

    booking_id = str(payload.get("booking_id") or payload.get("name") or "").strip()
    if not booking_id:
        frappe.throw("booking_id is required")

    rows = frappe.db.sql(
        """
        SELECT
            name,
            booking_no,
            vineyard,
            tour_package,
            tour_type,
            participants_count,
            scheduled_at,
            guest_name,
            guest_phone,
            guest_email,
            status,
            IFNULL(stock_deducted, 0) AS stock_deducted,
            checkin_at,
            completed_at,
            cancel_reason,
            notes,
            modified
        FROM `tabTour Booking`
        WHERE name = %s
        LIMIT 1
        """,
        (booking_id,),
        as_dict=True,
    )
    if not rows:
        frappe.throw("Booking not found")

    booking = rows[0]
    vineyard = booking["vineyard"]
    require_vineyard_permission(vineyard, mutate=False)

    package_wines = _get_package_wines(booking["tour_package"])
    movements = frappe.db.sql(
        """
        SELECT
            name,
            creation,
            vineyard_item,
            movement_type,
            qty_before,
            qty_delta,
            qty_after,
            reason,
            actor_user
        FROM `tabStock Movement`
        WHERE tour_booking = %s
        ORDER BY creation ASC
        """,
        (booking_id,),
        as_dict=True,
    )

    return {
        "status": "success",
        "booking": booking,
        "package_wines": package_wines,
        "movements": movements,
    }
