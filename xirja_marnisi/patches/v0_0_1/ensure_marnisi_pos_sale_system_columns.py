from __future__ import annotations

import frappe

from xirja_marnisi.api import bridge


def execute() -> None:
    bridge._ensure_sales_tables()
    frappe.db.commit()

