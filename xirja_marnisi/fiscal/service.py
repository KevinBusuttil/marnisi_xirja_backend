"""Atomic Marnisi fiscal ingestion, original-linked refunds and evidence reads.

All SQL data values are bound. Per-vineyard settings row locks serialize competing
sale identities, counters, refunds and manual references without request-time DDL.
Frappe owns the outer request transaction; successful batch members survive a failed
member's savepoint, and no acknowledgement is committed independently of its sale.
"""
from __future__ import annotations
import json
from datetime import date

import frappe
from frappe.exceptions import QueryDeadlockError, QueryTimeoutError

from xirja_marnisi.api.security import require_pos_access, require_authenticated_user
from xirja_marnisi.fiscal.core import amount, digest, encode, identity, money, number, refund_allocation
from xirja_marnisi.fiscal.validation import validate_sale

VINEYARD = "Marnisi M'Xlokk"
REGISTER = VINEYARD + '-MAIN'
SETTING_FIELDS = ('name', 'vineyard', 'mode', 'legal_name', 'vat_number', 'address', 'exo_number', 'currency',
                 'approval_reference', 'approval_confirmed', 'catalogue_reviewed', 'all_terminals_upgraded',
                 'legacy_queue_cleared', 'backup_restore_verified', 'physical_acceptance_confirmed',
                 'outage_procedure_confirmed', 'issuance_suspended', 'recovery_reference')
LIVE_CHECKS = ('approval_confirmed', 'catalogue_reviewed', 'all_terminals_upgraded', 'legacy_queue_cleared',
               'backup_restore_verified', 'physical_acceptance_confirmed', 'outage_procedure_confirmed')


def _load(raw):
    return json.loads(raw) if isinstance(raw, str) else raw


def settings_for(vineyard=VINEYARD, *, lock=False):
    rows = frappe.db.sql('SELECT ' + ','.join('`' + key + '`' for key in SETTING_FIELDS)
                        + ' FROM `tabMarnisi Fiscal Settings` WHERE vineyard=%s' + (' FOR UPDATE' if lock else ''),
                        (vineyard,), as_dict=True)
    return rows[0] if rows else None


def public_context(settings):
    if not settings:
        context = {'enabled': False, 'mode': 'Disabled', 'vineyard': VINEYARD, 'legal_name': '',
                   'vat_number': '', 'address': '', 'exo_number': '', 'currency': 'EUR'}
    else:
        context = {key: settings.get(key) or '' for key in ('vineyard', 'mode', 'legal_name', 'vat_number', 'address', 'exo_number', 'currency')}
        context['enabled'] = context['mode'] in ('Test', 'Live')
    context['revision'] = digest(context)
    return context


def get_public_context(vineyard=VINEYARD):
    return public_context(settings_for(vineyard))


def validate_settings(settings):
    if settings.get('vineyard') != VINEYARD or settings.get('currency') != 'EUR':
        raise ValueError('Fiscal settings require the locked Marnisi vineyard and EUR')
    if settings.get('mode') not in ('Disabled', 'Test', 'Live'):
        raise ValueError('Invalid fiscal mode')
    if settings.get('mode') == 'Live':
        if any(not str(settings.get(key) or '').strip() for key in ('legal_name', 'vat_number', 'address', 'exo_number', 'approval_reference')):
            raise ValueError('Verified seller, VAT, EXO and approval reference are required for Live')
        if any(not settings.get(key) for key in LIVE_CHECKS):
            raise ValueError('Live requires all catalogue, tablet, queue, restore, physical and outage approvals')


def _catalogue_reasons(payload):
    lines = payload['items']
    ids = sorted({line['si_id'] for line in lines})
    rows = frappe.db.sql('SELECT name,vineyard,item_code,item_name,sell_price,vat_rate,is_enabled '
                        'FROM `tabVineyard Item` WHERE vineyard=%s AND item_code IN (' + ','.join(['%s'] * len(ids)) + ') FOR UPDATE',
                        (VINEYARD, *(identifier.split('::', 1)[-1] for identifier in ids)), as_dict=True)
    catalogue = {VINEYARD + '::' + row['item_code']: row for row in rows}
    reasons = []
    for line in lines:
        item = catalogue.get(line['si_id'])
        if not line['si_id'].startswith(VINEYARD + '::') or not item:
            raise ValueError('Item does not belong to the locked vineyard catalogue')
        if not item.get('is_enabled'):
            reasons.append('Catalogue item is now disabled')
        if line['si_name'] != item['item_name']:
            reasons.append('Printed item description differs from current catalogue')
        if number(line['si_tax_pct']) != number(item['vat_rate']):
            reasons.append('Printed VAT differs from current server-owned catalogue VAT')
        printed_gross = money(number(line['si_price']) * (1 + number(line['si_tax_pct']) / 100))
        current_gross = money(number(item['sell_price']) * (1 + number(item['vat_rate']) / 100))
        if printed_gross != current_gross:
            reasons.append('Printed unit price differs from current catalogue')
    return list(dict.fromkeys(reasons))


def _insert(doctype, data):
    """Only service-owned constant table/field names reach this internal helper."""
    fields = list(data)
    frappe.db.sql('INSERT INTO `tab' + doctype + '` (' + ','.join('`' + field + '`' for field in fields)
                  + ') VALUES (' + ','.join(['%s'] * len(fields)) + ')', tuple(data[field] for field in fields))


def _base(name, actor, now):
    return {'name': name, 'creation': now, 'modified': now, 'modified_by': actor, 'owner': actor, 'docstatus': 0, 'idx': 0}


def receipts_for_sale(name):
    return frappe.db.sql('SELECT name,source_sale,source_payment,payment_uid,payment_amount,record_status,review_reason,mode,sequence_year,sequence_number '
                        'FROM `tabMarnisi Fiscal Receipt` WHERE source_sale=%s ORDER BY sequence_number,name', (name,), as_dict=True)


def _next_sequence(vineyard, mode, year):
    key = digest({'vineyard': vineyard, 'mode': mode, 'year': year})
    # The settings lock is already held; this row lock also protects maintenance callers.
    frappe.db.sql('INSERT IGNORE INTO `tabMarnisi Fiscal Counter` (name,vineyard,mode,sequence_year,last_number) VALUES (%s,%s,%s,%s,0)',
                  (key, vineyard, mode, year))
    row = frappe.db.sql('SELECT last_number FROM `tabMarnisi Fiscal Counter` WHERE name=%s FOR UPDATE', (key,))[0]
    sequence = int(row[0]) + 1
    frappe.db.sql('UPDATE `tabMarnisi Fiscal Counter` SET last_number=%s WHERE name=%s', (sequence, key))
    return sequence


def _existing(sale_num):
    rows = frappe.db.sql('SELECT name,sale_num,fiscal_protocol,fiscal_payload_hash,fiscal_status,acknowledgement,sales_payload '
                        'FROM `tabMarnisi POS Sale` WHERE sale_num=%s FOR UPDATE', (sale_num,), as_dict=True)
    return rows[0] if rows else None


def _refund_state(original):
    sale_num = original['sales_num']
    rows = frappe.db.sql('SELECT sales_payload FROM `tabMarnisi POS Sale` WHERE return_source_sale_num=%s FOR UPDATE', (sale_num,), as_dict=True)
    quantities, gross, net, paid = {}, {}, {}, {}
    for row in rows:
        refunded = _load(row['sales_payload'])
        for line in refunded['items']:
            source = line['return_source_line_uid']
            quantities[source] = quantities.get(source, number(0)) - number(line['si_qty'])
            gross[source] = gross.get(source, money(0)) - amount(line['si_total'])
            net[source] = net.get(source, money(0)) - amount(line['si_subtotal'])
        for payment in refunded['sale_pay_methods']:
            source = payment['return_source_payment_uid']
            paid[source] = paid.get(source, money(0)) - amount(payment['amount_tendered'])
    return quantities, gross, net, paid


def _has_manual_link(sale_name):
    return bool(frappe.db.sql('SELECT name FROM `tabMarnisi Manual Receipt Link` WHERE source_sale=%s FOR UPDATE', (sale_name,)))


def _validate_refund(payload, mode):
    source = _existing(identity(payload.get('return_source_sale_num'), 'original sale'))
    if not source or source.get('fiscal_protocol') != 1:
        raise ValueError('Legacy or unknown sales cannot be refunded electronically')
    if source.get('fiscal_status') not in ('Test', 'Recorded', 'Disabled'):
        raise ValueError('Review or manual transactions require reconciliation before refund')
    if mode != {'Recorded': 'Live', 'Test': 'Test', 'Disabled': 'Disabled'}[source['fiscal_status']]:
        raise ValueError('Refunds cannot cross Disabled, Test and Live fiscal modes')
    if _has_manual_link(source['name']):
        raise ValueError('Manually receipted transactions require manual reconciliation')
    original = _load(source['sales_payload'])
    if original.get('return_source_sale_num') or original.get('manual_receipt_reference'):
        raise ValueError('Refunds require an original electronic sale')
    if not str(payload.get('return_reason') or '').strip():
        raise ValueError('A refund reason is required')
    quantities, used_gross, used_net, used_paid = _refund_state(original)
    lines = {row['line_uid']: row for row in original['items']}
    payments = {row['payment_uid']: row for row in original['sale_pay_methods']}
    seen = set()
    for line in payload['items']:
        uid = line.get('return_source_line_uid')
        source_line = lines.get(uid)
        if not source_line or uid in seen:
            raise ValueError('Unknown or repeated original refund line')
        seen.add(uid)
        for key in ('si_id', 'si_name', 'si_price', 'si_tax_pct'):
            if str(line[key]) != str(source_line[key]) and digest(line[key]) != digest(source_line[key]):
                raise ValueError('Refund item evidence differs from the original sale')
        qty = -number(line['si_qty'])
        gross = refund_allocation(source_line['si_qty'], source_line['si_total'], quantities.get(uid, 0), used_gross.get(uid, 0), qty)
        net = refund_allocation(source_line['si_qty'], source_line['si_subtotal'], quantities.get(uid, 0), used_net.get(uid, 0), qty)
        if amount(line['si_total']) != -gross or amount(line['si_subtotal']) != -net or amount(line['si_tax']) != -(gross - net):
            raise ValueError('Refund amounts differ from the original cumulative allocation')
    seen.clear()
    for payment in payload['sale_pay_methods']:
        uid = payment.get('return_source_payment_uid')
        source_payment = payments.get(uid)
        if not source_payment or uid in seen:
            raise ValueError('Unknown or repeated original payment')
        seen.add(uid)
        if str(payment['tender_type_id']) != str(source_payment['tender_type_id']) or payment['payment_name'] != source_payment['payment_name']:
            raise ValueError('Refund tender differs from the original payment')
        if -amount(payment['amount_tendered']) + used_paid.get(uid, 0) > amount(source_payment['amount_tendered']):
            raise ValueError('Refund exceeds original tender amount')
    # Original snapshots are authoritative for refunded catalogue evidence.
    return original


def _manual_reference(reference):
    normalized = str(reference or '').strip().upper()
    if not normalized or len(normalized) > 120 or any(ord(char) < 32 for char in normalized):
        raise ValueError('A valid manual receipt reference is required')
    return normalized


def _create_manual_link(sale_name, reference, reason, actor, now, *, payload_hash='', reserved_payload=None):
    reference = _manual_reference(reference)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Manual receipt linkage requires a reason')
    existing = frappe.db.sql('SELECT name,source_sale,manual_reference,reason,payload_hash FROM `tabMarnisi Manual Receipt Link` WHERE vineyard=%s AND manual_reference=%s FOR UPDATE',
                             (VINEYARD, reference), as_dict=True)
    if existing:
        if existing[0]['source_sale'] == sale_name and existing[0]['reason'] == reason and existing[0].get('payload_hash', '') == payload_hash:
            return existing[0]['name']
        raise ValueError('Manual receipt reference is already linked')
    key = digest({'vineyard': VINEYARD, 'manual_reference': reference})
    _insert('Marnisi Manual Receipt Link', {**_base(key, actor, now), 'vineyard': VINEYARD, 'source_sale': sale_name,
                                          'manual_reference': reference, 'reason': reason, 'payload_hash': payload_hash,
                                          'reserved_payload': encode(reserved_payload) if reserved_payload else None,
                                          'is_reservation': int(reserved_payload is not None)})
    return key


def ingest_one(payload, *, refund=False):
    actor = require_authenticated_user()
    if not isinstance(payload, dict):
        raise ValueError('Sale must be an object')
    require_pos_access(payload.get('sales_store'), payload.get('sales_registerId'), mutate=True, staff_allowed=not refund)
    if payload.get('sales_store') != VINEYARD or payload.get('sales_registerId') != REGISTER:
        raise ValueError('The locked vineyard and register are required')
    sale_num = identity(payload.get('sales_num'), 'sale')
    if payload.get('sales_cashier') != actor:
        raise ValueError('Authenticate as the original cashier to synchronize this frozen sale')
    settings = settings_for(lock=True)
    if not settings:
        raise ValueError('Fiscal migration/settings installation is required before uploads')
    # This vineyard lock prevents concurrent missing-row races and over-refunds.
    existing = _existing(sale_num)
    payload_hash = digest(payload)
    if existing:
        if existing.get('fiscal_payload_hash') != payload_hash or not existing.get('acknowledgement'):
            raise ValueError('Conflicting retry or historical sale identity; existing financial evidence was not changed')
        return _load(existing['acknowledgement'])
    parent = 'MPS-' + sale_num
    reservations = frappe.db.sql('SELECT manual_reference,payload_hash FROM `tabMarnisi Manual Receipt Link` WHERE source_sale=%s AND is_reservation=1 FOR UPDATE', (parent,), as_dict=True)
    reservation = reservations[0] if reservations else None
    if reservation and reservation['payload_hash'] != payload_hash:
        raise ValueError('Frozen payload differs from the manager-approved manual reservation')
    validate_sale(payload, refund=refund)
    validate_settings(settings)
    manual = reservation['manual_reference'] if reservation else payload.get('manual_receipt_reference')
    if settings.get('issuance_suspended') and not manual:
        raise ValueError('Fiscal issuance is suspended pending recovery high-watermark reconciliation')
    if manual:
        if not reservation:
            require_pos_access(mutate=True)
        if refund:
            raise ValueError('Manual-reference refunds require manual reconciliation')
    if refund:
        _validate_refund(payload, settings['mode'])
        reasons = []
    else:
        reasons = _catalogue_reasons(payload)
    context = payload['receipt_snapshot']['fiscal_context']
    if context != public_context(settings):
        reasons.append('Frozen seller/fiscal settings differ from current settings; review stale/offline context')
    if payload['receipt_snapshot'].get('return_source_sale_num', '') != payload.get('return_source_sale_num', ''):
        raise ValueError('Printed original sale reference differs from refund linkage')
    mode = settings['mode']
    status = 'Manual' if manual else 'Review' if reasons else 'Recorded' if mode == 'Live' else mode
    now = frappe.utils.now()
    parent = 'MPS-' + sale_num
    _insert('Marnisi POS Sale', {**_base(parent, actor, now), 'sale_num': sale_num, 'sales_store': VINEYARD,
        'sales_register_id': REGISTER, 'sales_date': payload['sales_date'], 'sales_time': payload['sales_time'],
        'sales_cashier': actor, 'sales_total': amount(payload['sales_total']), 'loy_cust_card_num': '',
        'sales_payload': encode(payload), 'fiscal_protocol': 1, 'fiscal_final_total': amount(payload['fiscal_final_total']),
        'fiscal_payload_hash': payload_hash, 'receipt_snapshot': encode(payload['receipt_snapshot']), 'fiscal_status': status,
        'return_source_sale_num': payload.get('return_source_sale_num') or None,
        'manual_receipt_reference': _manual_reference(manual) if manual else None})
    for index, line in enumerate(payload['items'], 1):
        data = {key: line.get(key) for key in ('si_id', 'si_name', 'si_unit', 'si_barcode', 'si_category', 'si_qty', 'si_price', 'si_tax_pct',
                                              'si_subtotal', 'si_tax', 'si_total', 'si_discount_amount', 'si_discount_percent', 'line_uid', 'return_source_line_uid')}
        _insert('Marnisi POS Sale Item', {**_base('MPSI-' + line['line_uid'], actor, now), **data, 'parent': parent,
            'parentfield': 'sales_items', 'parenttype': 'Marnisi POS Sale', 'idx': index, 'si_sale_num': sale_num, 'item_payload': encode(line)})
    for index, payment in enumerate(payload['sale_pay_methods'], 1):
        payment_name = 'MPSP-' + payment['payment_uid']
        _insert('Marnisi POS Sale Payment', {**_base(payment_name, actor, now), 'parent': parent, 'parentfield': 'sales_payments',
            'parenttype': 'Marnisi POS Sale', 'idx': index, 'pay_txn_sale_num': sale_num, 'payment_uid': payment['payment_uid'],
            'tender_type_id': str(payment['tender_type_id']), 'payment_name': payment['payment_name'],
            'amount_tendered': amount(payment['amount_tendered']), 'return_source_payment_uid': payment.get('return_source_payment_uid'),
            'payment_payload': encode(payment)})
        if not manual and (mode != 'Disabled' or reasons):
            receipt_mode = 'Live' if status == 'Recorded' else 'Test'
            year = int(str(now)[:4])  # Issuance year, never a client-controlled historical year.
            sequence = _next_sequence(VINEYARD, receipt_mode, year)
            receipt_name = f'MF-{receipt_mode.upper()}-{year}-{sequence:08d}'
            _insert('Marnisi Fiscal Receipt', {**_base(receipt_name, actor, now), 'source_sale': parent, 'sale_number': sale_num,
                'source_payment': payment_name, 'payment_uid': payment['payment_uid'], 'vineyard': VINEYARD, 'register_id': REGISTER,
                'cashier': actor, 'payment_amount': amount(payment['amount_tendered']), 'payment_method': payment['payment_name'],
                'record_status': status, 'direction': 'Refund' if refund else 'Receipt', 'mode': receipt_mode, 'sequence_year': year,
                'sequence_number': sequence, 'currency': 'EUR', 'review_reason': '; '.join(reasons), 'settings_revision': context.get('revision', ''),
                'payload_hash': payload_hash, 'snapshot': encode({'pos_receipt': payload['receipt_snapshot'], 'payment': payment})})
    if manual and not reservation:
        _create_manual_link(parent, manual, payload.get('manual_receipt_reason'), actor, now, payload_hash=payload_hash)
    receipts = receipts_for_sale(parent)
    # Convert Decimal responses now, so saved and original acknowledgements are identical JSON.
    receipts = _load(encode([{**row, 'payment_amount': float(row['payment_amount'])} for row in receipts]))
    acknowledgement = {'sale_num': sale_num, 'status': 'synchronized', 'payload_hash': payload_hash,
                       'fiscal_status': status, 'fiscal_receipts': receipts, 'receipt_snapshot': payload['receipt_snapshot']}
    if manual:
        acknowledgement['manual_receipt_reference'] = _manual_reference(manual)
    frappe.db.sql('UPDATE `tabMarnisi POS Sale` SET acknowledgement=%s WHERE name=%s', (encode(acknowledgement), parent))
    return acknowledgement


def ingest_batch(payload, *, refund=False):
    rows = payload.get('sales')
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError('sales must be an array of at most 100 frozen sales')
    # MariaDB/Frappe 16 may abort the *entire* transaction on a concurrent
    # snapshot conflict (1020), deadlock or lock timeout. Never return earlier
    # confirmations from that abandoned transaction or roll back a gone savepoint.
    # Replay the complete immutable batch and recompute every acknowledgement.
    for attempt in range(4):
        require_pos_access(mutate=True, staff_allowed=not refund)
        confirmations = []
        try:
            for index, row in enumerate(rows):
                savepoint = f'marnisi_sale_{index}'
                frappe.db.savepoint(savepoint)
                try:
                    confirmation = ingest_one(row, refund=refund)
                except (QueryDeadlockError, QueryTimeoutError):
                    raise
                except Exception as exc:
                    frappe.db.rollback(save_point=savepoint)
                    # Database errors can expose internal SQL; do not return them.
                    safe = str(exc) if isinstance(exc, (ValueError, frappe.PermissionError, frappe.AuthenticationError)) else 'Sale was not stored; integrity validation failed'
                    confirmation = {'sale_num': row.get('sales_num', '') if isinstance(row, dict) else '', 'status': 'error', 'message': safe}
                confirmations.append(confirmation)
            return {'confirmations': confirmations}
        except (QueryDeadlockError, QueryTimeoutError):
            frappe.db.rollback()
            if attempt == 3:
                return {'confirmations': [
                    {'sale_num': row.get('sales_num', '') if isinstance(row, dict) else '', 'status': 'error',
                     'message': 'Concurrent database activity prevented this batch; retry the same frozen payload'}
                    for row in rows]}


def refund_context(sale_num):
    require_pos_access(mutate=True)
    identity(sale_num, 'original sale')
    row = _existing(sale_num)
    if not row or row.get('fiscal_protocol') != 1 or row.get('fiscal_status') not in ('Test', 'Recorded', 'Disabled'):
        raise ValueError('Only original protocol-1 electronic sales can be refunded')
    context = get_public_context()
    if context['mode'] != {'Recorded': 'Live', 'Test': 'Test', 'Disabled': 'Disabled'}[row['fiscal_status']]:
        raise ValueError('Refunds cannot cross Disabled, Test and Live fiscal modes')
    if _has_manual_link(row['name']):
        raise ValueError('Manually receipted transactions require manual reconciliation')
    original = _load(row['sales_payload'])
    if original.get('return_source_sale_num') or original.get('manual_receipt_reference'):
        raise ValueError('Select an original electronic sale')
    quantities, gross, net, paid = _refund_state(original)
    return {'sale': original, 'fiscal_receipts': receipts_for_sale(row['name']),
            'lines': [{**line, 'remaining_qty': float(number(line['si_qty']) - quantities.get(line['line_uid'], 0)),
                       'refunded_qty': float(quantities.get(line['line_uid'], 0)),
                       'refunded_gross': float(gross.get(line['line_uid'], 0)), 'refunded_net': float(net.get(line['line_uid'], 0)),
                       'remaining_gross': float(amount(line['si_total']) - gross.get(line['line_uid'], 0)),
                       'remaining_net': float(amount(line['si_subtotal']) - net.get(line['line_uid'], 0))} for line in original['items']],
            'payments': [{**payment, 'remaining_amount': float(amount(payment['amount_tendered']) - paid.get(payment['payment_uid'], 0))}
                         for payment in original['sale_pay_methods']], 'fiscal_context': context}


def link_manual(sale_num, reference, reason):
    actor = require_pos_access(mutate=True)['user']
    settings_for(lock=True)
    row = _existing(identity(sale_num, 'sale'))
    if not row or row.get('fiscal_protocol') != 1:
        raise ValueError('Manual linkage requires a saved protocol-1 sale')
    receipts = receipts_for_sale(row['name'])
    if row['fiscal_status'] not in ('Disabled', 'Review', 'Manual') or any(r['record_status'] in ('Recorded', 'Test') for r in receipts):
        raise ValueError('An electronically issued transaction cannot also use a manual receipt')
    name = _create_manual_link(row['name'], reference, reason, actor, frappe.utils.now(), payload_hash=row.get('fiscal_payload_hash', ''))
    return {'status': 'success', 'sale_num': sale_num, 'manual_link': name, 'manual_receipt_reference': _manual_reference(reference)}


def reconciliation_rows(filters):
    require_pos_access(mutate=True)
    from_date, to_date = str(filters.get('from_date', '')), str(filters.get('to_date', ''))
    date.fromisoformat(from_date)
    date.fromisoformat(to_date)
    # Aggregate each evidence source independently before joining; split payments do not multiply totals.
    rows = frappe.db.sql('''SELECT s.sale_num,s.sales_date,s.fiscal_status,s.sales_total,s.fiscal_protocol,
        s.return_source_sale_num,COALESCE(m.manual_reference,s.manual_receipt_reference) AS manual_receipt_reference,
        COALESCE(i.total,0) AS item_total,COALESCE(p.total,0) AS payment_total,COALESCE(p.cnt,0) AS payment_count,
        COALESCE(r.total,0) AS receipt_total,COALESCE(r.cnt,0) AS receipt_count,COALESCE(r.reviews,0) AS review_count
        FROM `tabMarnisi POS Sale` s
        LEFT JOIN (SELECT parent,SUM(si_total) total FROM `tabMarnisi POS Sale Item` GROUP BY parent) i ON i.parent=s.name
        LEFT JOIN (SELECT parent,SUM(amount_tendered) total,COUNT(*) cnt FROM `tabMarnisi POS Sale Payment` GROUP BY parent) p ON p.parent=s.name
        LEFT JOIN (SELECT source_sale,SUM(payment_amount) total,COUNT(*) cnt,SUM(record_status='Review') reviews FROM `tabMarnisi Fiscal Receipt` GROUP BY source_sale) r ON r.source_sale=s.name
        LEFT JOIN `tabMarnisi Manual Receipt Link` m ON m.source_sale=s.name
        WHERE s.sales_store=%s AND s.sales_date BETWEEN %s AND %s ORDER BY s.sales_date,s.sale_num''',
        (VINEYARD, from_date, to_date), as_dict=True)
    for row in rows:
        issues = []
        if not row.get('fiscal_protocol'):
            issues.append('Historical non-certified evidence')
        for field in ('item_total', 'payment_total'):
            if money(row[field]) != money(row['sales_total']):
                issues.append(field + ' mismatch')
        expected = row['fiscal_status'] in ('Test', 'Recorded', 'Review') and not row['manual_receipt_reference']
        if expected and (money(row['receipt_total']) != money(row['payment_total']) or row['receipt_count'] != row['payment_count']):
            issues.append('Fiscal payment evidence mismatch')
        if row['review_count']:
            issues.append('Review required')
        row['reconciliation_status'] = '; '.join(issues) or ('Manual reference' if row['manual_receipt_reference'] else 'Matched')
    reservations = frappe.db.sql('''SELECT m.manual_reference,m.reserved_payload FROM `tabMarnisi Manual Receipt Link` m
        LEFT JOIN `tabMarnisi POS Sale` s ON s.name=m.source_sale
        WHERE s.name IS NULL AND m.is_reservation=1 AND m.vineyard=%s
        AND JSON_UNQUOTE(JSON_EXTRACT(m.reserved_payload,'$.sales_date')) BETWEEN %s AND %s''',
        (VINEYARD, from_date, to_date), as_dict=True)
    for reservation in reservations:
        payload = _load(reservation['reserved_payload'])
        total = float(amount(payload['fiscal_final_total']))
        rows.append({'sale_num': payload['sales_num'], 'sales_date': payload['sales_date'], 'fiscal_status': 'Manual reserved',
                     'fiscal_protocol': 1, 'sales_total': total, 'item_total': total, 'payment_total': total,
                     'payment_count': len(payload['sale_pay_methods']), 'receipt_total': 0, 'receipt_count': 0,
                     'review_count': 0, 'return_source_sale_num': '', 'manual_receipt_reference': reservation['manual_reference'],
                     'reconciliation_status': 'Manual reservation awaiting original cashier upload'})
    return rows


def suspend_for_recovery(reference):
    """First step after restoring any backup, before allowing tablet reconnection."""
    require_pos_access(mutate=True)
    if not str(reference or '').strip():
        raise ValueError('A recovery incident reference is required')
    settings = settings_for(lock=True)
    if not settings:
        raise ValueError('Fiscal settings are not installed')
    frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET issuance_suspended=1,recovery_reference=%s WHERE name=%s',
                  (reference, settings['name']))
    return {'status': 'suspended', 'recovery_reference': reference}


def resume_after_reconciliation(reference, high_watermarks):
    """Monotonically advance counters to externally verified pre-restore evidence.

    An operator must supply *every* mode/year in the database and any additional
    mode/year found on tablets/paper after the backup. Missing/offline devices block
    the operator's recovery approval; this API cannot infer destroyed evidence.
    """
    require_pos_access(mutate=True)
    settings = settings_for(lock=True)
    if not settings or not settings.get('issuance_suspended'):
        raise ValueError('Issuance must be suspended before recovery reconciliation')
    if not str(reference or '').strip() or reference == settings.get('recovery_reference'):
        raise ValueError('A separate completed recovery approval reference is required')
    if not isinstance(high_watermarks, list):
        raise ValueError('Explicit Test/Live annual high-watermarks are required')
    known = frappe.db.sql('SELECT mode,sequence_year,last_number FROM `tabMarnisi Fiscal Counter` WHERE vineyard=%s FOR UPDATE', (VINEYARD,), as_dict=True)
    maxima = {(row['mode'], int(row['sequence_year'])): int(row['last_number']) for row in known}
    for row in frappe.db.sql('SELECT mode,sequence_year,MAX(sequence_number) last_number FROM `tabMarnisi Fiscal Receipt` WHERE vineyard=%s GROUP BY mode,sequence_year', (VINEYARD,), as_dict=True):
        key = (row['mode'], int(row['sequence_year']))
        maxima[key] = max(maxima.get(key, 0), int(row['last_number']))
    supplied = {}
    for row in high_watermarks:
        mode, year, value = row.get('mode'), int(row.get('year', 0)), number(row.get('last_number'))
        key = (mode, year)
        if mode not in ('Test', 'Live') or not 2000 <= year <= 9999 or value < 0 or value != value.to_integral_value() or key in supplied:
            raise ValueError('Invalid or duplicate recovery high-watermark')
        supplied[key] = int(value)
    if maxima.keys() - supplied.keys() or any(supplied[key] < value for key, value in maxima.items()):
        raise ValueError('Recovery cannot omit or decrease any existing annual high-watermark')
    for (mode, year), value in supplied.items():
        key = digest({'vineyard': VINEYARD, 'mode': mode, 'year': year})
        frappe.db.sql('INSERT INTO `tabMarnisi Fiscal Counter` (name,vineyard,mode,sequence_year,last_number) VALUES (%s,%s,%s,%s,%s) '
                      'ON DUPLICATE KEY UPDATE last_number=GREATEST(last_number,VALUES(last_number))',
                      (key, VINEYARD, mode, year, value))
    frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET issuance_suspended=0,recovery_reference=%s WHERE name=%s',
                  (reference, settings['name']))
    return {'status': 'resumed', 'recovery_reference': reference, 'high_watermarks': high_watermarks}


def reserve_manual_receipt(payload, reference, reason):
    """Reserve paper evidence before an unchanged original-operator outbox uploads.

    The manager's reservation is separate from the cashier's frozen payload. Its
    unique sale/reference/hash constraints prevent a later ordinary sync issuing a
    second electronic receipt. The original cashier identity is never rewritten.
    """
    actor = require_pos_access(mutate=True)['user']
    if not isinstance(payload, dict):
        raise ValueError('The original frozen sale payload is required')
    require_pos_access(payload.get('sales_store'), payload.get('sales_registerId'), mutate=True)
    if payload.get('sales_store') != VINEYARD or payload.get('sales_registerId') != REGISTER:
        raise ValueError('The locked vineyard and register are required')
    validate_sale(payload)
    if payload.get('manual_receipt_reference'):
        raise ValueError('This frozen sale already contains a manual reference')
    sale_num = identity(payload.get('sales_num'), 'sale')
    payload_hash = digest(payload)
    if not settings_for(lock=True):
        raise ValueError('Fiscal settings installation is required')
    existing = _existing(sale_num)
    if existing:
        if existing.get('fiscal_payload_hash') != payload_hash:
            raise ValueError('Existing sale differs from the original frozen manual payload')
        if existing.get('fiscal_status') not in ('Disabled', 'Review', 'Manual'):
            raise ValueError('Electronic evidence already issued; manual duplication requires reconciliation')
        link_manual(sale_num, reference, reason)
    else:
        _create_manual_link('MPS-' + sale_num, reference, reason, actor, frappe.utils.now(),
                            payload_hash=payload_hash, reserved_payload=payload)
    return {'status': 'reserved', 'sale_num': sale_num, 'payload_hash': payload_hash,
            'manual_receipt_reference': _manual_reference(reference)}
