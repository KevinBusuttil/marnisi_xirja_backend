from __future__ import annotations

from typing import Any

import frappe

from xirja_marnisi.api.security import require_authenticated_user


_DEFAULT_RECEIPT_SETTINGS: dict[str, Any] = {
    "receipt_line_width": 48,
    "receipt_currency_label": "EUR",
    "show_store_header": True,
    "show_client_details": True,
    "show_cash_summary": True,
    "show_vat_analysis": True,
    "show_opening_hours": True,
    "show_loyalty_section": True,
    "vat_message_line": "All items Include VAT.",
    "fiscal_message_line": "This is a Fiscal Receipt.",
    "thank_you_line": "Thanks for your custom.",
    "gift_receipt_title": "Gift Receipt",
    "gift_receipt_footer": "Enjoy your custom",
}


def _to_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0

    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "y", "on"}


def _to_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
        return parsed if parsed > 0 else default
    except Exception:
        return default


def _to_text(value: Any, default: str) -> str:
    text = str(value or "").strip()
    return text if text else default


def _load_receipt_settings() -> dict[str, Any]:
    field_names = tuple(_DEFAULT_RECEIPT_SETTINGS.keys())
    placeholders = ", ".join(["%s"] * len(field_names))

    rows = frappe.db.sql(
        f"""
        SELECT field, value
        FROM `tabSingles`
        WHERE doctype = 'Marnisi Settings'
          AND field IN ({placeholders})
        """,
        field_names,
        as_dict=True,
    )

    values = {str(row.get("field") or ""): row.get("value") for row in rows}

    return {
        "receipt_line_width": _to_int(
            values.get("receipt_line_width"),
            _DEFAULT_RECEIPT_SETTINGS["receipt_line_width"],
        ),
        "receipt_currency_label": _to_text(
            values.get("receipt_currency_label"),
            _DEFAULT_RECEIPT_SETTINGS["receipt_currency_label"],
        ),
        "show_store_header": _to_bool(
            values.get("show_store_header"),
            _DEFAULT_RECEIPT_SETTINGS["show_store_header"],
        ),
        "show_client_details": _to_bool(
            values.get("show_client_details"),
            _DEFAULT_RECEIPT_SETTINGS["show_client_details"],
        ),
        "show_cash_summary": _to_bool(
            values.get("show_cash_summary"),
            _DEFAULT_RECEIPT_SETTINGS["show_cash_summary"],
        ),
        "show_vat_analysis": _to_bool(
            values.get("show_vat_analysis"),
            _DEFAULT_RECEIPT_SETTINGS["show_vat_analysis"],
        ),
        "show_opening_hours": _to_bool(
            values.get("show_opening_hours"),
            _DEFAULT_RECEIPT_SETTINGS["show_opening_hours"],
        ),
        "show_loyalty_section": _to_bool(
            values.get("show_loyalty_section"),
            _DEFAULT_RECEIPT_SETTINGS["show_loyalty_section"],
        ),
        "vat_message_line": _to_text(
            values.get("vat_message_line"),
            _DEFAULT_RECEIPT_SETTINGS["vat_message_line"],
        ),
        "fiscal_message_line": _to_text(
            values.get("fiscal_message_line"),
            _DEFAULT_RECEIPT_SETTINGS["fiscal_message_line"],
        ),
        "thank_you_line": _to_text(
            values.get("thank_you_line"),
            _DEFAULT_RECEIPT_SETTINGS["thank_you_line"],
        ),
        "gift_receipt_title": _to_text(
            values.get("gift_receipt_title"),
            _DEFAULT_RECEIPT_SETTINGS["gift_receipt_title"],
        ),
        "gift_receipt_footer": _to_text(
            values.get("gift_receipt_footer"),
            _DEFAULT_RECEIPT_SETTINGS["gift_receipt_footer"],
        ),
    }


@frappe.whitelist()
def get_receipt_settings(args: str = "") -> dict[str, Any]:
    require_authenticated_user()
    return {
        "status": "success",
        "settings": _load_receipt_settings(),
    }
