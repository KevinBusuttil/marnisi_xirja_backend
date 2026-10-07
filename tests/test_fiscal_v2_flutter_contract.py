"""Actual Dart-generated v2 checkout amounts, discounts and frozen hashes."""
import json
import os
from pathlib import Path
import pytest
from xirja_marnisi.fiscal.core import digest, money
from xirja_marnisi.fiscal.validation import validate_sale
from xirja_marnisi.fiscal.validation_v2 import cart_hash

FIXTURE = Path(__file__).parent / 'fixtures' / 'marnisi_fiscal_v2_contract.json'
EXAMPLES = json.loads(FIXTURE.read_text())


@pytest.mark.parametrize('example', EXAMPLES, ids=lambda row: row['payload']['sales_num'])
def test_dart_v2_financial_evidence_matches_python_validation_and_hashes(example):
    payload = example['payload']
    assert validate_sale(payload)['total'] == money(payload['fiscal_final_total'])
    assert digest(payload) == example['payload_hash']
    assert cart_hash(payload) == example['cart_hash']


def test_v2_fixture_has_not_drifted_across_repositories():
    sibling = Path(os.environ.get('MARNISI_FRONTEND_PATH', Path(__file__).resolve().parents[4] / 'XIRJA-frontend-marnisi'))
    fixture = sibling / 'test' / 'fixtures' / FIXTURE.name
    if fixture.exists():
        assert json.loads(fixture.read_text()) == EXAMPLES
