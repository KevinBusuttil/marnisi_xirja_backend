"""Deny generic CRUD of financial evidence, including child-only operations."""
import frappe
from frappe.model.document import Document
from xirja_marnisi.api.security import require_pos_access
from xirja_marnisi.fiscal.service import validate_settings


class ImmutableEvidence(Document):
    def _deny(self, *args, **kwargs):
        frappe.throw('Financial evidence is immutable; use the authenticated Marnisi fiscal APIs', frappe.PermissionError)

    before_insert = _deny
    validate = _deny
    before_save = _deny
    before_submit = _deny
    before_cancel = _deny
    on_trash = _deny
    before_rename = _deny
    # Frappe db_set calls on_change after writing; deny before the write so a
    # caller catching the exception cannot later commit altered evidence.
    before_change = _deny
    on_change = _deny


class FiscalSettings(Document):
    def validate(self):
        require_pos_access(mutate=True)
        validate_settings(self.as_dict())
        old = self.get_doc_before_save()
        if old and old.issuance_suspended and not self.issuance_suspended:
            frappe.throw('Use the recovery reconciliation API to resume issuance', frappe.PermissionError)

    def on_trash(self):
        frappe.throw('Fiscal settings and numbering history cannot be deleted', frappe.PermissionError)

    def before_rename(self, *args, **kwargs):
        frappe.throw('Fiscal settings cannot be renamed', frappe.PermissionError)
