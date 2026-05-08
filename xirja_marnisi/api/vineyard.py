from __future__ import annotations

from typing import Any

import frappe

from xirja_marnisi.api.security import get_accessible_vineyards, require_authenticated_user


@frappe.whitelist()
def list_assigned(args: str = "") -> dict[str, Any]:
    user = require_authenticated_user()
    rows = get_accessible_vineyards(user)

    vineyards = []
    for row in rows:
        vineyard = row.get("vineyard")
        if not vineyard:
            continue

        meta = frappe.db.sql(
            """
            SELECT
                name,
                vineyard_code,
                vineyard_name,
                IFNULL(is_active, 1) AS is_active,
                timezone,
                contact_email,
                contact_phone
            FROM `tabVineyard`
            WHERE name = %s
            LIMIT 1
            """,
            (vineyard,),
            as_dict=True,
        )
        if not meta:
            continue

        vineyards.append(
            {
                **meta[0],
                "access_role": row.get("access_role") or "Super Admin",
                "is_default": int(row.get("is_default") or 0) == 1,
            }
        )

    return {
        "status": "success",
        "vineyards": vineyards,
        "count": len(vineyards),
    }
