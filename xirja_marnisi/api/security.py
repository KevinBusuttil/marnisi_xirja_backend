from __future__ import annotations

import json
from typing import Any

import frappe

ADMIN_MUTATION_ROLES = {"Super Admin", "System Manager", "Vineyard Admin"}
STAFF_MUTATION_ROLES = ADMIN_MUTATION_ROLES | {"Vineyard Staff"}


def parse_args(args: str | dict[str, Any] | None = None) -> dict[str, Any]:
    """Normalize payloads coming from Frappe, HTTP JSON, or wrapper {"args": "..."}."""
    payload: dict[str, Any] = {}

    if isinstance(args, str) and args.strip():
        payload = json.loads(args)
    elif isinstance(args, dict):
        payload = dict(args)

    if not payload and hasattr(frappe, "request") and getattr(frappe, "request"):
        try:
            request_json = frappe.request.get_json(silent=True)
        except Exception:
            request_json = None
        if isinstance(request_json, dict):
            payload = dict(request_json)

    if not payload and hasattr(frappe, "form_dict"):
        form_dict = getattr(frappe, "form_dict", None)
        if isinstance(form_dict, dict):
            payload = dict(form_dict)

    nested_args = payload.get("args")
    if isinstance(nested_args, str) and nested_args.strip():
        payload = json.loads(nested_args)
    elif isinstance(nested_args, dict):
        payload = dict(nested_args)

    return payload


def require_authenticated_user() -> str:
    user = getattr(frappe.session, "user", "Guest")
    if not user or user == "Guest":
        frappe.throw("Authentication required", frappe.AuthenticationError)
    return user


def get_roles(user: str | None = None) -> set[str]:
    user_id = user or getattr(frappe.session, "user", "Guest")
    try:
        return set(frappe.get_roles(user_id))
    except Exception:
        return set()


def is_global_admin(user: str | None = None) -> bool:
    roles = get_roles(user)
    return "System Manager" in roles or "Super Admin" in roles


def get_access_rows(user: str) -> list[dict[str, Any]]:
    return frappe.db.sql(
        """
        SELECT
            vineyard,
            access_role,
            is_default,
            is_active
        FROM `tabVineyard User Access`
        WHERE `user` = %s
          AND IFNULL(is_active, 1) = 1
        ORDER BY is_default DESC, modified DESC
        """,
        (user,),
        as_dict=True,
    )


def get_accessible_vineyards(user: str) -> list[dict[str, Any]]:
    if is_global_admin(user):
        return frappe.db.sql(
            """
            SELECT
                name AS vineyard,
                'Super Admin' AS access_role,
                1 AS is_default,
                1 AS is_active
            FROM `tabVineyard`
            WHERE IFNULL(is_active, 1) = 1
            ORDER BY vineyard_name ASC
            """,
            as_dict=True,
        )
    return get_access_rows(user)


def resolve_vineyard(payload: dict[str, Any], user: str) -> str:
    requested = (payload.get("vineyard") or payload.get("vineyard_id") or "").strip()
    rows = get_accessible_vineyards(user)

    if requested:
        allowed = {row["vineyard"] for row in rows}
        if requested not in allowed:
            frappe.throw("You are not allowed to access this vineyard", frappe.PermissionError)
        return requested

    if not rows:
        frappe.throw("No vineyard access configured for this user", frappe.PermissionError)

    defaults = [row for row in rows if int(row.get("is_default") or 0) == 1]
    chosen = defaults[0] if defaults else rows[0]
    return chosen["vineyard"]


def require_vineyard_permission(vineyard: str, mutate: bool = False, staff_allowed: bool = False) -> dict[str, Any]:
    user = require_authenticated_user()

    if is_global_admin(user):
        return {
            "user": user,
            "role": "Super Admin",
            "vineyard": vineyard,
            "is_global_admin": True,
        }

    rows = get_access_rows(user)
    for row in rows:
        if row.get("vineyard") != vineyard:
            continue

        role = row.get("access_role") or "Viewer"
        if not mutate:
            return {
                "user": user,
                "role": role,
                "vineyard": vineyard,
                "is_global_admin": False,
            }

        if role in ADMIN_MUTATION_ROLES:
            return {
                "user": user,
                "role": role,
                "vineyard": vineyard,
                "is_global_admin": False,
            }

        if staff_allowed and role in STAFF_MUTATION_ROLES:
            return {
                "user": user,
                "role": role,
                "vineyard": vineyard,
                "is_global_admin": False,
            }

        frappe.throw("You do not have permission to modify this vineyard", frappe.PermissionError)

    frappe.throw("You are not assigned to this vineyard", frappe.PermissionError)


def now_ts() -> str:
    return frappe.utils.now()


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False
