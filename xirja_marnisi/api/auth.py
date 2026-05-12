from __future__ import annotations

from typing import Any

import frappe

from xirja_marnisi.api.bridge import get_all_users
from xirja_marnisi.api.security import (
    get_accessible_vineyards,
    get_roles,
    parse_args,
    require_authenticated_user,
)


def _build_personal_login_map() -> dict[str, str]:
    users = get_all_users()
    login_map: dict[str, str] = {}

    for row in users:
        personal_id = str(row.get("retail_personnel_id") or "").strip()
        email = str(row.get("retail_user_email") or "").strip()
        if not personal_id or not email:
            continue
        login_map[personal_id] = email

    return login_map


_PERSONAL_LOGIN_MAP = _build_personal_login_map()

_DEFAULT_SEED_PASSWORD = "Marnisi@2026#Seed!"


def _column_exists(table_name: str, column_name: str) -> bool:
    rows = frappe.db.sql(
        """
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return bool(rows)


def _get_vineyard_ui_assets(vineyard_name: str) -> dict[str, str]:
    out = {
        "login_background_image": "",
        "app_background_image": "",
    }
    if not vineyard_name:
        return out

    select_parts = []
    if _column_exists("tabVineyard", "pos_login_background_image"):
        select_parts.append("pos_login_background_image AS login_background_image")
    else:
        select_parts.append("'' AS login_background_image")

    if _column_exists("tabVineyard", "pos_app_background_image"):
        select_parts.append("pos_app_background_image AS app_background_image")
    else:
        select_parts.append("'' AS app_background_image")

    row = frappe.db.sql(
        f"""
        SELECT {", ".join(select_parts)}
        FROM `tabVineyard`
        WHERE name = %s
        LIMIT 1
        """,
        (vineyard_name,),
        as_dict=True,
    )
    if not row:
        return out

    first = row[0]
    out["login_background_image"] = str(first.get("login_background_image") or "").strip()
    out["app_background_image"] = str(first.get("app_background_image") or "").strip()
    return out


@frappe.whitelist()
def get_context() -> dict[str, Any]:
    """Return authenticated session context with roles and vineyard assignments."""
    user = require_authenticated_user()
    roles = sorted(get_roles(user))
    access_rows = get_accessible_vineyards(user)

    vineyards: list[dict[str, Any]] = []
    default_vineyard = ""

    for row in access_rows:
        vineyard_name = row.get("vineyard")
        if not vineyard_name:
            continue

        entry = {
            "vineyard": vineyard_name,
            "access_role": row.get("access_role") or "Super Admin",
            "is_default": int(row.get("is_default") or 0) == 1,
        }
        vineyards.append(entry)

        if entry["is_default"] and not default_vineyard:
            default_vineyard = vineyard_name

    if not default_vineyard and vineyards:
        default_vineyard = vineyards[0]["vineyard"]

    ui_assets = _get_vineyard_ui_assets(default_vineyard)

    return {
        "status": "success",
        "user": user,
        "roles": roles,
        "vineyards": vineyards,
        "default_vineyard": default_vineyard,
        "ui_assets": ui_assets,
    }


@frappe.whitelist(allow_guest=True)
def login_with_personal_id(args: str = "") -> dict[str, Any]:
    """Create a Frappe session using a personal number used by POS login."""
    payload = parse_args(args)
    personal_id = str(payload.get("personal_id") or "").strip()
    if not personal_id:
        frappe.throw("Personal ID is required")

    user = _PERSONAL_LOGIN_MAP.get(personal_id)
    if not user:
        frappe.throw("Unknown Personal ID")

    login_manager = getattr(frappe.local, "login_manager", None)
    if not login_manager:
        frappe.throw("Login manager unavailable")

    login_manager.authenticate(user=user, pwd=_DEFAULT_SEED_PASSWORD)
    login_manager.post_login()

    return {
        "status": "success",
        "user": user,
    }
