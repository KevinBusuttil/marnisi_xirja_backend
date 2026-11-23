"""Protocol 2 checkout and durable, explicitly confirmed cash disbursement.

Raw service-owned tables have no generic DocType CRUD endpoints. Cash payout events
are append-only; sale snapshots/acknowledgements are never changed after issuance.
The settings lock serializes checkout identities and handovers across terminals.
"""
import secrets
from xirja_marnisi.fiscal import diagnostics as diag
from xirja_marnisi.fiscal import service as s
from xirja_marnisi.fiscal.core import amount, digest, encode, identity, money, number
from xirja_marnisi.fiscal.validation_v2 import cart_hash, validate_sale_v2


def _actor(payload=None):
    if payload is not None and not isinstance(payload, dict):
        raise ValueError('A frozen checkout object is required')
    diag.stage('original_cashier')
    actor = s.require_authenticated_user()
    diag.stage('original_cashier', authenticated_user=actor)
    diag.stage('pos_authorization')
    s.require_pos_access(s.VINEYARD, s.REGISTER, mutate=True, staff_allowed=True)
    if payload is not None:
        if payload.get('sales_store') != s.VINEYARD or payload.get('sales_registerId') != s.REGISTER:
            raise ValueError('The locked vineyard and register are required')
        if payload.get('sales_cashier') != actor:
            raise ValueError('Authenticate as the original cashier for this checkout')
    return actor


def _settings():
    diag.stage('fiscal_settings')
    settings = s.settings_for(lock=True)
    if not settings:
        raise ValueError('Fiscal migration/settings installation is required')
    diag.stage('fiscal_settings', mode=settings.get('mode'), suspended=bool(settings.get('issuance_suspended')))
    diag.settings_facts(settings)
    s.validate_settings(settings)
    if settings.get('issuance_suspended'):
        raise ValueError('Fiscal issuance is suspended pending recovery reconciliation')
    return settings


def _catalogue(payload):
    ids = sorted({line['si_id'] for line in payload['items']})
    rows = s.frappe.db.sql('SELECT item_code,item_name,sell_price,vat_rate,is_enabled,is_returnable,allow_variable_price '
        'FROM `tabVineyard Item` WHERE vineyard=%s AND item_code IN (' + ','.join(['%s'] * len(ids)) + ') FOR UPDATE',
        (s.VINEYARD, *(key.split('::', 1)[-1] for key in ids)), as_dict=True)
    diag.catalogue(payload, rows, s.VINEYARD)
    catalogue = {s.VINEYARD + '::' + row['item_code']: row for row in rows}
    reasons = []
    for line in payload['items']:
        item = catalogue.get(line['si_id'])
        diag.stage('catalogue_item', item_id=line['si_id'], line_uid=line.get('line_uid'), matched=item is not None)
        if not item:
            raise ValueError('Item does not belong to the locked vineyard catalogue')
        if line['line_kind'] == 'return' and not item.get('is_returnable'):
            raise ValueError('This item is not configured as returnable')
        variable = bool(item.get('allow_variable_price'))
        if (line['price_mode'] == 'open') != variable:
            raise ValueError('Variable pricing is not configured for this item')
        if not item.get('is_enabled'):
            reasons.append('Catalogue item is now disabled')
        if line['si_name'] != item['item_name']:
            reasons.append('Printed item name differs from current catalogue')
        if number(line['si_tax_pct']) != number(item['vat_rate']):
            reasons.append('Printed VAT differs from server-owned catalogue VAT')
        if not variable and amount(line['unit_gross']) != money(number(item['sell_price']) * (1 + number(item['vat_rate']) / 100)):
            reasons.append('Printed unit price differs from current catalogue')
    return list(dict.fromkeys(reasons))


def _authorization(sale_num):
    rows = s.frappe.db.sql('SELECT sale_num,cart_hash,authorization,cashier,fiscal_mode FROM `tabMarnisi Checkout Authorization` WHERE sale_num=%s FOR UPDATE', (sale_num,), as_dict=True)
    return rows[0] if rows else None


def prepare_checkout(payload):
    actor = _actor(payload)
    validate_sale_v2(payload, preflight=True)
    settings = _settings()
    sale_num = payload['sales_num']
    if s._existing(sale_num):
        raise ValueError('Checkout is already recorded; recover the existing sale')
    existing = _authorization(sale_num)
    frozen_hash = cart_hash(payload)
    if existing:
        if existing['cashier'] != actor or existing['cart_hash'] != frozen_hash:
            raise ValueError('Checkout identity was already authorized for different cart facts')
        if existing['fiscal_mode'] != settings['mode']:
            raise ValueError('Authorized checkout cannot cross fiscal modes')
        token = existing['authorization']
    else:
        reasons = _catalogue(payload)
        if payload['receipt_snapshot']['fiscal_context'] != s.public_context(settings):
            reasons.append('Refresh the fiscal settings before accepting payment')
        if reasons:
            raise ValueError('; '.join(reasons))
        token = secrets.token_urlsafe(32)
        s.frappe.db.sql('INSERT INTO `tabMarnisi Checkout Authorization` (sale_num,cart_hash,authorization,cashier,fiscal_mode,created_at) VALUES (%s,%s,%s,%s,%s,%s)',
            (sale_num, frozen_hash, token, actor, settings['mode'], s.frappe.utils.now()))
    return {'status': 'prepared', 'sale_num': sale_num, 'cart_hash': frozen_hash, 'authorization': token, 'fiscal_context': s.public_context(settings)}


def _proof(row):
    return {key: row.get(key) or '' for key in ('payout_uid', 'sale_num', 'state', 'handover_uid')} | {'amount': float(amount(row['amount']))}


def _payout(sale_num, payout_uid):
    rows = s.frappe.db.sql('SELECT payout_uid,sale_num,amount,state,handover_uid,cashier,fiscal_mode FROM `tabMarnisi Cash Payout` WHERE sale_num=%s AND payout_uid=%s FOR UPDATE', (sale_num, payout_uid), as_dict=True)
    if not rows:
        raise ValueError('Unknown cash payout identity')
    return rows[0]


def ingest_checkout(payload):
    actor = _actor(payload)
    sale_num = identity(payload.get('sales_num'), 'sale')
    # Exact retries are independent of current catalogue, mode and recovery changes.
    settings = s.settings_for(lock=True)
    existing = s._existing(sale_num)
    payload_hash = digest(payload)
    diag.stage('existing_sale', present=bool(existing), received_hash=payload_hash,
               stored_hash=existing.get('fiscal_payload_hash') if existing else None)
    if existing:
        if existing.get('fiscal_payload_hash') != payload_hash or not existing.get('acknowledgement'):
            raise ValueError('Conflicting retry; existing financial evidence was not changed')
        return s._load(existing['acknowledgement'])
    diag.stage('financial_validation')
    values = validate_sale_v2(payload)
    settings = _settings()
    authorization = _authorization(sale_num)
    token = payload.get('checkout_authorization')
    diag.authorization(payload, authorization, token, cart_hash)
    if values['has_returns'] and not token:
        raise ValueError('Return-containing carts require online checkout authorization before settlement')
    if token:
        if not authorization or not secrets.compare_digest(str(token), authorization['authorization']) or authorization['cart_hash'] != cart_hash(payload) or authorization['cashier'] != actor or authorization['fiscal_mode'] != settings['mode']:
            raise ValueError('Invalid authorization or changed frozen cart')
        # Facts validated before money was accepted remain authoritative after a catalogue edit.
        reasons = []
    else:
        reasons = _catalogue(payload)
    context = payload['receipt_snapshot']['fiscal_context']
    diag.stage('fiscal_context', matches=context == s.public_context(settings),
               saved_revision=context.get('revision'), current_revision=s.public_context(settings).get('revision'))
    if context != s.public_context(settings):
        reasons.append('Frozen seller/fiscal settings differ from current settings; review required')
    mode = settings['mode']
    status = 'Review' if reasons else 'Recorded' if mode == 'Live' else mode
    diag.stage('persistence', fiscal_status=status)
    now = s.frappe.utils.now()
    parent = 'MPS-' + sale_num
    s._insert('Marnisi POS Sale', {**s._base(parent, actor, now), 'sale_num': sale_num, 'sales_store': s.VINEYARD,
        'sales_register_id': s.REGISTER, 'sales_date': payload['sales_date'], 'sales_time': payload['sales_time'],
        'sales_cashier': actor, 'sales_total': values['total'], 'loy_cust_card_num': '', 'sales_payload': encode(payload),
        'fiscal_protocol': 2, 'fiscal_final_total': values['total'], 'fiscal_payload_hash': payload_hash,
        'receipt_snapshot': encode(payload['receipt_snapshot']), 'fiscal_status': status})
    for index, line in enumerate(payload['items'], 1):
        data = {key: line.get(key) for key in ('si_id', 'si_name', 'si_unit', 'si_barcode', 'si_category', 'si_qty', 'si_price', 'si_tax_pct', 'si_subtotal', 'si_tax', 'si_total', 'si_discount_amount', 'si_discount_percent', 'line_uid')}
        s._insert('Marnisi POS Sale Item', {**s._base('MPSI-' + line['line_uid'], actor, now), **data,
            'parent': parent, 'parentfield': 'sales_items', 'parenttype': 'Marnisi POS Sale', 'idx': index,
            'si_sale_num': sale_num, 'item_payload': encode(line)})
    for index, payment in enumerate(payload['sale_pay_methods'], 1):
        s._insert('Marnisi POS Sale Payment', {**s._base('MPSP-' + payment['payment_uid'], actor, now),
            'parent': parent, 'parentfield': 'sales_payments', 'parenttype': 'Marnisi POS Sale', 'idx': index,
            'pay_txn_sale_num': sale_num, 'payment_uid': payment['payment_uid'], 'tender_type_id': str(payment['tender_type_id']),
            'payment_name': payment['payment_name'], 'amount_tendered': amount(payment['amount_tendered']), 'payment_payload': encode(payment)})
    if mode != 'Disabled' or reasons:
        receipt_mode = 'Live' if status == 'Recorded' else 'Test'
        year = int(str(now)[:4])
        sequence = s._next_sequence(s.VINEYARD, receipt_mode, year)
        receipt_name = f'MF-{receipt_mode.upper()}-{year}-{sequence:08d}'
        s._insert('Marnisi Fiscal Receipt', {**s._base(receipt_name, actor, now), 'source_sale': parent,
            'sale_number': sale_num, 'source_payment': None, 'payment_uid': None, 'record_kind': 'Transaction',
            'vineyard': s.VINEYARD, 'register_id': s.REGISTER, 'cashier': actor, 'payment_amount': values['total'],
            'payment_method': 'Cash refund' if values['total'] < 0 else 'No payment' if not values['total'] else 'Customer payment',
            'record_status': status, 'direction': payload['settlement']['direction'], 'mode': receipt_mode,
            'sequence_year': year, 'sequence_number': sequence, 'currency': 'EUR', 'review_reason': '; '.join(reasons),
            'settings_revision': context.get('revision', ''), 'payload_hash': payload_hash,
            'snapshot': encode({'pos_receipt': payload['receipt_snapshot'], 'settlement': payload['settlement']})})
    receipts = s._load(encode([{**row, 'payment_amount': float(row['payment_amount'])} for row in s.receipts_for_sale(parent)]))
    acknowledgement = {'sale_num': sale_num, 'status': 'synchronized', 'payload_hash': payload_hash,
        'fiscal_status': status, 'fiscal_receipts': receipts, 'receipt_snapshot': payload['receipt_snapshot'], 'settlement': payload['settlement']}
    if values['total'] < 0:
        payout_uid = payload['settlement']['payout_uid']
        s.frappe.db.sql('INSERT INTO `tabMarnisi Cash Payout` (payout_uid,sale_num,amount,state,handover_uid,cashier,fiscal_mode,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
            (payout_uid, sale_num, -values['total'], 'pending', '', actor, mode, now))
        acknowledgement['cash_payout'] = {'payout_uid': payout_uid, 'sale_num': sale_num, 'amount': float(-values['total']), 'state': 'pending', 'handover_uid': ''}
    s.frappe.db.sql('UPDATE `tabMarnisi POS Sale` SET acknowledgement=%s WHERE name=%s', (encode(acknowledgement), parent))
    return acknowledgement


def payout_action(payload, action):
    if action not in ('status', 'begin', 'confirm'):
        raise ValueError('Invalid cash payout action')
    for attempt in range(4):
        try:
            return _payout_action(payload, action)
        except (s.QueryDeadlockError, s.QueryTimeoutError):
            s.frappe.db.rollback()
            if attempt == 3:
                raise ValueError('Concurrent activity prevented cash confirmation; retry the same identity')


def _payout_action(payload, action):
    actor = _actor()
    sale_num = identity(payload.get('sale_num'), 'sale')
    payout_uid = identity(payload.get('payout_uid'), 'cash payout')
    settings = s.settings_for(lock=True)
    row = _payout(sale_num, payout_uid)
    if row['cashier'] != actor:
        raise ValueError('Authenticate as the original cashier to resolve this handover')
    sale = s._existing(sale_num)
    if not sale or sale['fiscal_protocol'] != 2:
        raise ValueError('An accepted protocol 2 sale is required')
    if action == 'status':
        return {'status': 'success', 'cash_payout': _proof(row)}
    if sale['fiscal_status'] not in ('Test', 'Recorded', 'Disabled'):
        raise ValueError('Review or unaccepted checkout cannot authorize cash handover')
    handover = identity(payload.get('handover_uid'), 'cash handover')
    # Repeated confirmations remain resolvable through later fiscal settings changes.
    if row['state'] != 'pending' and row['handover_uid'] != handover:
        raise ValueError('Cash handover already started; recover its existing identity')
    target = 'started' if action == 'begin' else 'confirmed'
    if row['state'] == 'confirmed' or row['state'] == target:
        return {'status': 'success', 'cash_payout': _proof(row)}
    if action == 'confirm' and row['state'] != 'started':
        raise ValueError('Begin the durable handover before confirming cash given')
    if action == 'begin':
        if not settings or settings.get('issuance_suspended') or settings['mode'] != row['fiscal_mode']:
            raise ValueError('Restore fiscal mode/recovery before starting a cash handover')
    now = s.frappe.utils.now()
    s.frappe.db.sql('INSERT INTO `tabMarnisi Cash Payout Event` (name,payout_uid,event_type,handover_uid,cashier,created_at) VALUES (%s,%s,%s,%s,%s,%s)',
        (digest({'payout_uid': payout_uid, 'event_type': target}), payout_uid, target, handover, actor, now))
    s.frappe.db.sql('UPDATE `tabMarnisi Cash Payout` SET state=%s,handover_uid=%s WHERE payout_uid=%s', (target, handover, payout_uid))
    row = {**row, 'state': target, 'handover_uid': handover}
    return {'status': 'success', 'cash_payout': _proof(row)}
