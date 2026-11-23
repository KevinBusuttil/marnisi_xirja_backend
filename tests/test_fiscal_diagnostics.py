"""Diagnostics must explain failures without changing issuance or exposing secrets."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from xirja_marnisi.fiscal import diagnostics as d, service
from test_checkout_v2 import db, configured


@pytest.fixture
def events(monkeypatch):
    events = []
    logger = SimpleNamespace(error=lambda text: events.append(json.loads(text)),
                             info=lambda text: events.append(json.loads(text)),
                             setLevel=lambda level: None)
    monkeypatch.setattr(service.frappe, 'logger', lambda *a, **k: logger, raising=False)
    return events


def test_missing_item_captures_stage_entire_financial_projection_and_actual_query_rows(db, events):
    sale = configured(db, purchase=70, returned=0)
    before = deepcopy(sale)
    db.catalogue = []
    sale['password'] = 'password-secret'
    sale['checkout_authorization_unused'] = 'token-secret'
    result = service.ingest_batch({'sales': [sale]})['confirmations'][0]
    event = events[-1]
    assert result['status'] == 'error'
    assert event['stage'] == 'catalogue_item'
    assert event['catalogue_item']['matched'] is False
    assert event['catalogue_item']['item_id'] == sale['items'][0]['si_id']
    assert event['catalogue']['rows'] == []
    assert event['catalogue']['requested_ids'] == [sale['items'][0]['si_id']]
    assert event['saved_sale']['items']['rows'][0]['unit_gross'] == 70
    assert event['saved_sale']['snapshot']['total'] == 70
    assert event['saved_sale']['payments']['rows'][0]['tender_type_id'] == '1'
    assert event['exception_type'] == 'ValueError'
    assert event['frames'][-1]['function'] == '_catalogue'
    assert event['attempt_id'] and event['elapsed_ms'] >= 0
    assert 'password-secret' not in json.dumps(event) and 'token-secret' not in json.dumps(event)
    assert sale['receipt_snapshot'] == before['receipt_snapshot']
    assert not db.tables['Marnisi POS Sale'] and not db.sequence


def test_catalogue_pricing_failure_captures_current_and_frozen_facts(db, events):
    sale = configured(db, purchase=70, returned=0)
    db.catalogue[0]['sell_price'] = 999
    result = service.ingest_batch({'sales': [sale]})['confirmations'][0]
    event = events[-1]
    assert result['fiscal_status'] == 'Review'
    # Direct catalogue capture includes exactly the rows the validator used.
    with d.capture() as details:
        d.catalogue(sale, db.catalogue, service.VINEYARD)
        assert details['catalogue']['rows'][0]['sell_price'] == 999
        assert details['catalogue']['rows'][0]['canonical_id'] == sale['items'][0]['si_id']
        assert details['catalogue']['rows'][0]['item_name_fingerprint'] == d.fingerprint('Purchase')
    assert event['fiscal_status'] == 'Review'
    assert event['catalogue']['rows'][0]['sell_price'] == 999
    assert 'Printed unit price differs' in event['review_reasons'][0]


def test_invalid_snapshot_total_is_identified_before_catalogue_or_storage(db, events):
    sale = configured(db, purchase=70, returned=0)
    sale['receipt_snapshot']['tax'] = 999
    result = service.ingest_batch({'sales': [sale]})['confirmations'][0]
    event = events[-1]
    assert result['status'] == 'error'
    assert event['stage'] == 'financial_validation'
    assert event['saved_sale']['snapshot']['tax'] == 999
    assert event['saved_sale']['sales_tax'] != 999
    assert not db.tables['Marnisi POS Sale']


def test_success_logs_generated_ack_separately_from_request_commit(db, events):
    callbacks = []
    db.after_commit = SimpleNamespace(add=callbacks.append)
    sale = configured(db, purchase=70, returned=0)
    ack = service.ingest_batch({'sales': [sale]})['confirmations'][0]
    event = events[-1]
    assert event['event'] == 'sale_acknowledgement_generated'
    assert event['transaction_state'] == 'awaiting_request_commit'
    assert event['payload_hash'] == ack['payload_hash']
    assert event['receipt_ids'] == [ack['fiscal_receipts'][0]['name']]
    assert not any(e['event'] == 'sale_request_committed' for e in events)
    callbacks[0]()
    assert events[-1]['event'] == 'sale_request_committed'
    assert events[-1]['attempt_id'] == event['attempt_id']
    assert 'saved_sale' not in event  # Successful carts are not copied to logs.


def test_logger_failure_does_not_change_success_or_failure(db, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OSError('logging failed')
    monkeypatch.setattr(service.frappe, 'logger', unavailable, raising=False)
    sale = configured(db, purchase=70, returned=0)
    assert service.ingest_batch({'sales': [sale]})['confirmations'][0]['status'] == 'synchronized'
    invalid = deepcopy(sale)
    invalid['sales_cashier'] = 'wrong@example.invalid'
    assert service.ingest_batch({'sales': [invalid]})['confirmations'][0]['status'] == 'error'


def test_unexpected_db_error_logs_frames_and_type_but_never_sql_or_locals(db, events):
    sale = configured(db, purchase=70, returned=0)
    db.failed_table = 'Marnisi POS Sale Payment'
    result = service.ingest_batch({'sales': [sale]})['confirmations'][0]
    event = events[-1]
    assert result['status'] == 'error'
    assert event['exception_type'] == 'RuntimeError'
    assert event['stage'] == 'persistence'
    assert 'late write failed' not in json.dumps(event)
    assert all(set(frame) == {'file', 'line', 'function'} for frame in event['frames'])
    assert not db.tables['Marnisi POS Sale'] and not db.sequence


def test_projection_redacts_unknown_fields_tokens_and_malformed_nested_objects():
    sale = {'sales_num': 'sale', 'password': 'root-secret', 'sid': 'cookie-secret',
            'checkout_authorization': 'auth-secret',
            'items': [{'si_id': 'id', 'si_price': {'password': 'nested-secret'},
                       'si_name': 'private wine name', 'description': 'private description',
                       'password': 'line-secret'}],
            'sale_pay_methods': [{'payment_uid': 'p', 'token': 'payment-secret'}]}
    text = json.dumps(d.payload_summary(sale))
    for secret in ('root-secret', 'cookie-secret', 'auth-secret', 'nested-secret',
                   'line-secret', 'payment-secret', 'private wine name', 'private description'):
        assert secret not in text
    assert d.payload_summary(sale)['authorization_present'] is True


def test_all_contract_item_rows_are_retained_and_over_limit_is_explicit():
    row = {'items': [{'si_id': str(i)} for i in range(501)]}
    items = d.payload_summary(row)['items']
    assert len(items['rows']) == 500 and items['count'] == 501 and items['truncated']
    row['items'].pop()
    assert not d.payload_summary(row)['items']['truncated']


def test_nested_capture_restores_scope_and_authorization_diagnostic_cannot_raise():
    with d.capture() as first:
        d.stage('outer')
        with d.capture() as second:
            d.stage('inner')
            assert first['attempt_id'] != second['attempt_id']
        d.stage('restored')
        assert first['stage'] == 'restored' and second['stage'] == 'inner'
        def bad_hash(_):
            raise ValueError('must never affect checkout')
        d.authorization({}, None, None, bad_hash)
        assert first['checkout_authorization']['diagnostic_unavailable']
    d.stage('no scope')
    assert first['stage'] == 'checkout_authorization'


def test_batch_authorization_failure_is_logged_and_still_raises(db, events, monkeypatch):
    sale = configured(db, purchase=70, returned=0)
    def denied(*args, **kwargs):
        raise PermissionError('No vineyard access configured')
    monkeypatch.setattr(service, 'require_pos_access', denied)
    with pytest.raises(PermissionError):
        service.ingest_batch({'sales': [sale]})
    assert events[-1]['stage'] == 'batch_authorization'
    assert not db.tables['Marnisi POS Sale']
