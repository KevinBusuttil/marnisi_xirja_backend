from __future__ import annotations

import json
from pathlib import Path

from xirja_marnisi.api import settings


class _FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or []

    def sql(self, query: str, values=None, as_dict: bool = False):
        normalized = " ".join(query.split())
        if "FROM `tabSingles`" in normalized:
            return self.rows if as_dict else []
        return []


class _FakeFrappe:
    def __init__(self, rows=None):
        self.db = _FakeDB(rows=rows)


def _doctype_json_path() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "xirja_marnisi"
        / "xirja_marnisi"
        / "doctype"
        / "marnisi_settings"
        / "marnisi_settings.json"
    )


def test_marnisi_settings_doctype_is_single():
    data = json.loads(_doctype_json_path().read_text())
    assert data["name"] == "Marnisi Settings"
    assert data["issingle"] == 1
    fieldnames = {field["fieldname"] for field in data["fields"]}
    assert "receipt_currency_label" in fieldnames
    assert "show_vat_analysis" in fieldnames


def test_load_receipt_settings_uses_defaults_when_singles_are_missing(monkeypatch):
    monkeypatch.setattr(settings, "frappe", _FakeFrappe(rows=[]))

    resolved = settings._load_receipt_settings()

    assert resolved == {
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


def test_load_receipt_settings_applies_singles_overrides(monkeypatch):
    monkeypatch.setattr(
        settings,
        "frappe",
        _FakeFrappe(
            rows=[
                {"field": "receipt_line_width", "value": "42"},
                {"field": "receipt_currency_label", "value": "USD"},
                {"field": "show_vat_analysis", "value": "0"},
                {"field": "show_store_header", "value": "false"},
                {"field": "show_opening_hours", "value": "no"},
                {"field": "thank_you_line", "value": "Grazie"},
                {"field": "gift_receipt_footer", "value": "Gift ready"},
            ]
        ),
    )

    resolved = settings._load_receipt_settings()

    assert resolved["receipt_line_width"] == 42
    assert resolved["receipt_currency_label"] == "USD"
    assert resolved["show_vat_analysis"] is False
    assert resolved["show_store_header"] is False
    assert resolved["show_opening_hours"] is False
    assert resolved["thank_you_line"] == "Grazie"
    assert resolved["gift_receipt_footer"] == "Gift ready"
    assert resolved["show_client_details"] is True


def test_get_receipt_settings_returns_success_payload(monkeypatch):
    monkeypatch.setattr(settings, "frappe", _FakeFrappe(rows=[]))
    monkeypatch.setattr(settings, "require_authenticated_user", lambda: "admin@example.com")

    result = settings.get_receipt_settings()

    assert result["status"] == "success"
    assert result["settings"]["receipt_currency_label"] == "EUR"
