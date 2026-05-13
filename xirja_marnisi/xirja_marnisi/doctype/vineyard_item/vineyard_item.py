from __future__ import annotations

import frappe
from frappe.model.document import Document

_DEFAULT_ITEM_IMAGE = "assets/items/1.png"


def _file_url_from_name(file_name: str) -> str:
    row = frappe.db.sql(
        """
        SELECT file_url
        FROM `tabFile`
        WHERE name = %s
        LIMIT 1
        """,
        (file_name,),
        as_dict=True,
    )
    if not row:
        return ""
    return str(row[0].get("file_url") or "").strip()


def _file_name_from_url(file_url: str) -> str:
    row = frappe.db.sql(
        """
        SELECT name
        FROM `tabFile`
        WHERE file_url = %s
        ORDER BY modified DESC
        LIMIT 1
        """,
        (file_url,),
        as_dict=True,
    )
    if not row:
        return ""
    return str(row[0].get("name") or "").strip()


class VineyardItem(Document):
    def validate(self) -> None:
        image_path = str(self.image_path or "").strip()
        image_file = str(getattr(self, "image_file", "") or "").strip()

        # Dropdown file selection can drive image path automatically.
        if image_file and not image_path:
            image_path = _file_url_from_name(image_file)
            self.image_path = image_path

        # Keep dropdown populated when image path maps to an uploaded file.
        if image_path and not image_file:
            detected_file_name = _file_name_from_url(image_path)
            if detected_file_name:
                self.image_file = detected_file_name

        if not str(self.image_path or "").strip():
            self.image_path = _DEFAULT_ITEM_IMAGE
