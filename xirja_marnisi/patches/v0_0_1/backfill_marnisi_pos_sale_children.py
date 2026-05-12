from __future__ import annotations

import frappe

from xirja_marnisi.api import bridge


def execute() -> None:
    bridge._backfill_sales_children_from_payload(limit=0)
    frappe.db.commit()
