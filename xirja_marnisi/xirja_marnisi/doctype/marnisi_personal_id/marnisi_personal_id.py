import frappe
from frappe.model.document import Document


class MarnisiPersonalID(Document):
    def validate(self):
        if frappe.session.user != "Administrator" and "System Manager" not in frappe.get_roles():
            frappe.throw("Only System Managers can manage Personal IDs", frappe.PermissionError)
        self.personal_id = str(self.personal_id or "").strip()
        if not self.personal_id or len(self.personal_id) > 64:
            frappe.throw("Personal ID must contain 1 to 64 characters")
        if self.user in {"Guest", "Administrator"}:
            frappe.throw("Personal ID requires an individual operator account")
