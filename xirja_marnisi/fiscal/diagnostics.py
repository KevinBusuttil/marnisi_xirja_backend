"""Private, bounded upload diagnostics; never changes financial validation.

No diagnostic SQL is issued. Catalogue facts are captured from the actual locked
validation query, so debugging cannot add locks or affect transaction outcomes.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from decimal import Decimal
import hashlib
import json
import logging
import math
import re
import time
import traceback
import uuid

_current = ContextVar('marnisi_upload_diagnostic', default=None)
_LIMIT = 500  # Same maximum as the fiscal contract's item/payment arrays.
_LINE = ('si_id', 'line_uid', 'si_code', 'si_barcode', 'si_sale_num', 'si_qty',
         'si_price', 'si_tax_pct', 'si_subtotal', 'si_tax', 'si_total',
         'si_discount_amount', 'si_discount_percent', 'line_kind', 'price_mode',
         'unit_gross', 'discount_type', 'discount_value', 'return_source_line_uid')
_PAYMENT = ('payment_uid', 'tender_type_id', 'payment_name', 'amount_tendered')
_CONTEXT = ('vineyard', 'enabled', 'mode', 'currency', 'revision')
_TOTALS = ('subtotal', 'tax', 'total', 'discount', 'change', 'cash_tendered')
_TOP = ('sales_num', 'fiscal_protocol', 'sales_store', 'sales_registerId',
        'sales_cashier', 'sales_date', 'sales_time', 'sales_total', 'sales_subtotal',
        'sales_tax', 'sales_discount_amount', 'sales_discount_percent',
        'sales_cash_tendered', 'sales_change', 'fiscal_final_total',
        'return_source_sale_num', 'document_type', 'pre_bill', 'is_prebill')


def _value(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (str, Decimal)):
        return str(value)[:500]
    return '<' + type(value).__name__ + '>'


def project(row, fields):
    return {key: _value(row[key]) for key in fields if key in row} if isinstance(row, dict) else {}


def fingerprint(value):
    text = str(value or '')
    return {'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest(), 'length': len(text)}


def _lines(rows):
    if not isinstance(rows, list):
        return {'invalid_type': type(rows).__name__}
    result = []
    for row in rows[:_LIMIT]:
        if not isinstance(row, dict):
            result.append({'invalid_type': type(row).__name__})
            continue
        item = project(row, _LINE)
        for key in ('si_name', 'description'):
            if key in row:
                item[key + '_fingerprint'] = fingerprint(row[key])
        description = row.get('description')
        if isinstance(description, str):
            item['description_validation'] = {
                'has_controls': any(ord(c) < 32 for c in description),
                'has_markup': bool(re.search(r'<[^>]*>', description)),
            }
        result.append(item)
    return {'count': len(rows), 'truncated': len(rows) > _LIMIT, 'rows': result}


def _payments(rows):
    if not isinstance(rows, list):
        return {'invalid_type': type(rows).__name__}
    return {'count': len(rows), 'truncated': len(rows) > _LIMIT,
            'rows': [project(row, _PAYMENT) for row in rows[:_LIMIT]]}


def payload_summary(row):
    row = row if isinstance(row, dict) else {}
    result = project(row, _TOP)
    result['items'] = _lines(row.get('items'))
    result['payments'] = _payments(row.get('sale_pay_methods'))
    result['discount_settings'] = project(row.get('discount_settings'), ('mode', 'type', 'value'))
    result['settlement'] = project(row.get('settlement'), ('direction', 'amount', 'payout_uid'))
    result['authorization_present'] = bool(row.get('checkout_authorization'))
    snapshot = row.get('receipt_snapshot')
    if isinstance(snapshot, dict):
        printed = project(snapshot, _TOTALS + ('sale_number', 'cashier', 'date', 'time', 'fiscal_protocol', 'document_kind'))
        printed['items'] = _lines(snapshot.get('items'))
        printed['payments'] = _payments(snapshot.get('payments'))
        printed['discount_settings'] = project(snapshot.get('discount_settings'), ('mode', 'type', 'value'))
        printed['settlement'] = project(snapshot.get('settlement'), ('direction', 'amount', 'payout_uid'))
        groups = snapshot.get('tax_rows')
        printed['tax_rows'] = [project(group, ('line_kind', 'rate', 'net', 'tax', 'gross')) for group in groups[:_LIMIT]] if isinstance(groups, list) else None
        context = snapshot.get('fiscal_context')
        printed['fiscal_context'] = project(context, _CONTEXT)
        if isinstance(context, dict):
            printed['seller_fingerprints'] = {key: fingerprint(context.get(key)) for key in ('legal_name', 'vat_number', 'address', 'exo_number')}
        result['snapshot'] = printed
    else:
        result['snapshot_type'] = type(snapshot).__name__
    return result


@contextmanager
def capture(attempt=1):
    details = {'schema': 2, 'attempt_id': uuid.uuid4().hex,
               'batch_attempt': attempt, 'stage': 'ingest', 'started': time.monotonic()}
    token = _current.set(details)
    try:
        yield details
    finally:
        _current.reset(token)


def stage(name, **facts):
    try:
        current = _current.get()
        if current is not None:
            current['stage'] = name
            if facts:
                current[name] = {key: _value(value) for key, value in facts.items()}
    except Exception:
        pass


def catalogue(payload, rows, vineyard):
    try:
        current = _current.get()
        if current is None:
            return
        items = payload.get('items', [])
        current['catalogue'] = {
            'locked_vineyard': vineyard,
            'requested_ids': [_value(item.get('si_id')) for item in items[:_LIMIT] if isinstance(item, dict)],
            'rows': [],
        }
        for row in rows[:_LIMIT]:
            value = project(row, ('name', 'vineyard', 'item_code', 'sell_price', 'vat_rate', 'is_enabled', 'is_returnable', 'allow_variable_price'))
            value['canonical_id'] = vineyard + '::' + str(row.get('item_code', ''))
            value['item_name_fingerprint'] = fingerprint(row.get('item_name'))
            current['catalogue']['rows'].append(value)
    except Exception:
        pass


def settings_facts(settings):
    try:
        current = _current.get()
        if current is None:
            return
        fields = ('mode', 'currency', 'vineyard', 'issuance_suspended',
                  'approval_confirmed', 'catalogue_reviewed', 'all_terminals_upgraded',
                  'legacy_queue_cleared', 'backup_restore_verified',
                  'physical_acceptance_confirmed', 'outage_procedure_confirmed')
        current['fiscal_settings'] = project(settings, fields)
        current['fiscal_settings']['seller_fingerprints'] = {
            key: fingerprint(settings.get(key))
            for key in ('legal_name', 'vat_number', 'address', 'exo_number')}
    except Exception:
        pass


def authorization(payload, stored, token, cart_hash):
    try:
        if _current.get() is None:
            return
        stage('checkout_authorization', token_present=bool(token), stored_present=bool(stored),
              stored_cart_hash=stored.get('cart_hash') if stored else None,
              received_cart_hash=cart_hash(payload),
              stored_cashier=stored.get('cashier') if stored else None,
              stored_mode=stored.get('fiscal_mode') if stored else None)
    except Exception:
        stage('checkout_authorization', diagnostic_unavailable=True)


def details_for_log(details, row, error=None):
    result = {key: value for key, value in (details or {}).items() if key != 'started'}
    result.setdefault('schema', 2)
    result.setdefault('attempt_id', uuid.uuid4().hex)
    if details and 'started' in details:
        result['elapsed_ms'] = round((time.monotonic() - details['started']) * 1000)
    result['saved_sale'] = payload_summary(row)
    if error is not None:
        # No exception text/locals/source lines: database errors can contain SQL.
        result['exception_type'] = type(error).__name__
        if error.args and isinstance(error.args[0], int):
            result['database_error_code'] = error.args[0]
        frames = traceback.extract_tb(error.__traceback__)[-12:]
        result['frames'] = [{'file': frame.filename.rsplit('/', 1)[-1],
                             'line': frame.lineno, 'function': frame.name} for frame in frames]
    return result


def emit(frappe, event, facts, *, rejected=False):
    try:
        logger = frappe.logger('marnisi_fiscal_sync', allow_site=True,
                               max_size=2 * 1024 * 1024, file_count=10)
        if hasattr(logger, 'setLevel'):
            logger.setLevel(logging.INFO)
        getattr(logger, 'error' if rejected else 'info')(
            json.dumps({'event': event, **facts}, ensure_ascii=True, sort_keys=True, allow_nan=False))
    except Exception:
        pass


def outcome(frappe, row, acknowledgement, details):
    try:
        receipts = acknowledgement.get('fiscal_receipts') or []
        facts = {'schema': 2, 'attempt_id': details['attempt_id'],
                 'sale_num': _value(row.get('sales_num')),
                 'status': _value(acknowledgement.get('status')),
                 'fiscal_status': _value(acknowledgement.get('fiscal_status')),
                 'payload_hash': _value(acknowledgement.get('payload_hash')),
                 'receipt_ids': [_value(receipt.get('name')) for receipt in receipts[:_LIMIT] if isinstance(receipt, dict)],
                 'transaction_state': 'awaiting_request_commit'}
        if acknowledgement.get('fiscal_status') == 'Review':
            facts.update(details_for_log(details, row))
            facts['review_reasons'] = [str(receipt.get('review_reason', ''))[:1000]
                                      for receipt in receipts[:_LIMIT] if isinstance(receipt, dict)]
        emit(frappe, 'sale_acknowledgement_generated', facts)
        hooks = getattr(frappe.db, 'after_commit', None)
        if hooks is not None and hasattr(hooks, 'add'):
            committed = {key: facts[key] for key in ('schema', 'attempt_id', 'sale_num',
                         'status', 'fiscal_status', 'payload_hash', 'receipt_ids')}
            hooks.add(lambda: emit(frappe, 'sale_request_committed', {
                **committed, 'transaction_state': 'committed'}))
    except Exception:
        pass
