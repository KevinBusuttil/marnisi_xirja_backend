"""Exercise the actual Frappe db_set hook ordering without a database."""
from types import SimpleNamespace

import pytest
from frappe.model import document

from xirja_marnisi.fiscal import documents


def test_rejected_db_set_never_reaches_database_write(monkeypatch):
    writes = []

    def deny(message, error):
        raise error(message)

    fake = SimpleNamespace(
        PermissionError=PermissionError,
        throw=deny,
        db=SimpleNamespace(set_value=lambda *args, **kwargs: writes.append((args, kwargs))),
    )
    monkeypatch.setattr(document, 'frappe', fake)
    monkeypatch.setattr(documents, 'frappe', fake)

    class EvidenceHarness:
        doctype = 'Marnisi Fiscal Receipt'
        name = 'SYNTHETIC-RECEIPT'
        meta = SimpleNamespace(istable=True, issingle=False)
        modified = None
        modified_by = None

        def set(self, field, value):
            setattr(self, field, value)

        def get_doc_before_save(self):
            return None

        def run_method(self, name):
            hook = getattr(documents.ImmutableEvidence, name, None)
            if hook:
                hook(self)

    with pytest.raises(PermissionError, match='immutable'):
        document.Document.db_set(EvidenceHarness(), 'payment_amount', 1, update_modified=False)
    assert writes == []
