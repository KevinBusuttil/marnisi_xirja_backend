from __future__ import annotations

from typing import Any

import frappe

from xirja_marnisi.api.settings import _load_receipt_settings
from xirja_marnisi.api.security import (
    get_accessible_vineyards,
    get_roles,
    parse_args,
    require_authenticated_user,
    LOCKED_STORE_ID,
)


def _csrf_token() -> str:
    from frappe.sessions import get_csrf_token

    return get_csrf_token()


def _public_fiscal_context(vineyard: str) -> dict[str, Any]:
    from xirja_marnisi.fiscal.service import get_public_context

    return get_public_context(vineyard) if vineyard else {"mode": "Disabled"}


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

    # The tablet is a single-vineyard register even when an operator has other assignments.
    if any(row["vineyard"] == LOCKED_STORE_ID for row in vineyards):
        default_vineyard = LOCKED_STORE_ID
    else:
        frappe.throw("No access to the locked POS vineyard", frappe.PermissionError)

    ui_assets = _get_vineyard_ui_assets(default_vineyard)
    receipt_settings = _load_receipt_settings()

    return {
        "status": "success",
        "user": user,
        "roles": roles,
        "vineyards": vineyards,
        "default_vineyard": default_vineyard,
        "ui_assets": ui_assets,
        "receipt_settings": receipt_settings,
        "csrf_token": _csrf_token(),
        "fiscal_context": _public_fiscal_context(default_vineyard),
    }


@frappe.whitelist(allow_guest=True, methods=["POST"])
def login_with_personal_id(args: str = "") -> dict[str, Any]:
    """Keep unexpected login failures out of contextual credential tracebacks.

    Frappe deliberately excludes AuthenticationError from Error Log snapshots.
    Serialized arguments and framework kwargs may still contain credentials even
    after form_dict is cleared, so unexpected parsing/database failures must cross
    this fail-closed boundary as a sanitized authentication error.
    """
    try:
        return _login_with_personal_id(args)
    except frappe.AuthenticationError:
        raise
    except Exception:
        raise frappe.AuthenticationError("Unable to complete POS login; please try again") from None


def _login_with_personal_id(args: str = "") -> dict[str, Any]:
    """Resolve an administrator-maintained ID, then use Frappe's full login flow.

    Never cache passwords or authenticate with demo credentials. Frappe owns
    throttling, disabled checks, password expiry, 2FA and session creation.
    """
    try:
        payload = parse_args(args)
    finally:
        # Frappe sanitizes named credential fields, but a serialized args wrapper
        # can otherwise expose the password in error metadata before login runs.
        for key in ("args", "password", "pwd"):
            frappe.form_dict.pop(key, None)

    from frappe.auth import get_login_attempt_tracker

    personal_id = str(payload.get("personal_id") or "").strip()
    password = payload.pop("password", None)
    tracker = get_login_attempt_tracker(frappe.local.request_ip)
    if not personal_id or not isinstance(password, str) or not password:
        tracker and tracker.add_failure_attempt()
        frappe.throw("Invalid login credentials", frappe.AuthenticationError)

    rows = frappe.db.sql(
        """SELECT m.`user` FROM `tabMarnisi Personal ID` m
           INNER JOIN `tabUser` u ON u.name = m.`user`
           WHERE m.personal_id = %s AND m.is_active = 1 AND u.enabled = 1
           LIMIT 2""",
        (personal_id,), as_dict=True,
    )
    if len(rows) != 1:
        tracker and tracker.add_failure_attempt()
        frappe.throw("Invalid login credentials", frappe.AuthenticationError)

    user = rows[0]["user"]
    login_manager = frappe.local.login_manager
    frappe.form_dict["usr"] = user
    frappe.form_dict["pwd"] = password
    try:
        completed = login_manager.login()
    finally:
        frappe.form_dict.pop("pwd", None)
        frappe.form_dict.pop("args", None)
    if completed is False or getattr(frappe.session, "user", "Guest") != user:
        frappe.throw("Complete the required account verification before POS login", frappe.AuthenticationError)

    return {
        "status": "success",
        "user": user,
        "csrf_token": _csrf_token(),
    }
