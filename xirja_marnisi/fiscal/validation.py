"""Validate sale and printer evidence independently of client assertions."""
from collections import Counter
from datetime import date, time
from decimal import Decimal

from xirja_marnisi.fiscal.core import amount, digest, identity, money, number

TENDERS = {'1': 'Cash', '7': 'Card BOV'}
LINE_KEYS = ('line_uid', 'si_id', 'si_name', 'si_qty', 'si_price', 'si_tax_pct',
             'si_subtotal', 'si_tax', 'si_total', 'si_discount_amount')
PAYMENT_KEYS = ('payment_uid', 'tender_type_id', 'payment_name', 'amount_tendered')


def _rows(payload, key):
    rows = payload.get(key)
    if not isinstance(rows, list) or not rows or len(rows) > 500:
        raise ValueError(f'{key} must contain between 1 and 500 rows')
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError(f'Invalid {key} row')
    return rows


def _project(row, keys):
    if not isinstance(row, dict) or any(key not in row for key in keys):
        raise ValueError('Incomplete printed financial row')
    return digest({key: row[key] for key in keys})


def validate_sale(payload, *, refund=False):
    if not isinstance(payload, dict) or type(payload.get('fiscal_protocol')) is not int or payload.get('fiscal_protocol') != 1:
        raise ValueError('Upgraded fiscal protocol 1 is required; retain legacy outbox for explicit recovery')
    sale_id = identity(payload.get('sales_num'), 'sale')
    date.fromisoformat(str(payload.get('sales_date', '')))
    time.fromisoformat(str(payload.get('sales_time', '')))
    if not isinstance(payload.get('sales_cashier'), str) or not payload['sales_cashier'].strip():
        raise ValueError('Cashier identity is required')
    if payload.get('pre_bill') or payload.get('is_prebill') or payload.get('document_type') == 'Pre-bill':
        raise ValueError('Pre-bills cannot be posted as completed sales')
    if bool(payload.get('return_source_sale_num')) != refund:
        raise ValueError('Refunds must use the original-linked refund endpoint')
    if payload.get('loy_cust_card_num') or payload.get('loy_points_used'):
        raise ValueError('Loyalty is not supported by Marnisi fiscal sales')
    items, payments = _rows(payload, 'items'), _rows(payload, 'sale_pay_methods')
    sign = -1 if refund else 1
    line_ids, payment_ids, groups = set(), set(), {}
    subtotal = tax = total = discount = money(0)
    for line in items:
        uid = identity(line.get('line_uid'), 'line')
        if uid in line_ids:
            raise ValueError('Duplicate line identity')
        line_ids.add(uid)
        if not line.get('si_id') or not str(line.get('si_name', '')).strip():
            raise ValueError('Item identity and description are required')
        if line.get('si_sale_num') not in (None, '', sale_id):
            raise ValueError('Item belongs to a different sale')
        qty, price, rate = number(line.get('si_qty')), number(line.get('si_price')), number(line.get('si_tax_pct'))
        if qty * sign <= 0 or qty != qty.to_integral_value() or price < 0 or not 0 <= rate <= 100:
            raise ValueError('Invalid quantity, unit price or VAT; mixed or arbitrary negative baskets are prohibited')
        net, vat, gross, reduction = [amount(line.get(key)) for key in ('si_subtotal', 'si_tax', 'si_total', 'si_discount_amount')]
        if reduction < 0 or net + vat != gross or gross * sign < 0:
            raise ValueError('Line totals or discounts are inconsistent')
        if not refund and line.get('return_source_line_uid'):
            raise ValueError('Original line linkage is only allowed on linked refunds')
        if not refund:
            # si_price preserves catalogue net precision (e.g. 2.966 -> 3.50 gross).
            calculated_gross = money(price * (1 + rate / 100)) * qty - reduction
            if reduction > money(price * (1 + rate / 100)) * qty or gross != calculated_gross or net != money(gross / (1 + rate / 100)):
                raise ValueError('Line net, VAT or gross does not match rounded inclusive unit price')
        elif reduction or not line.get('return_source_line_uid'):
            raise ValueError('Refund lines require original line references and allocated amounts')
        values = groups.setdefault(rate, [money(0), money(0), money(0)])
        for index, value in enumerate((net, vat, gross)):
            values[index] += value
        subtotal += net
        tax += vat
        total += gross
        discount += reduction
    paid = cash = money(0)
    for payment in payments:
        uid = identity(payment.get('payment_uid'), 'payment')
        if uid in payment_ids:
            raise ValueError('Duplicate payment identity')
        payment_ids.add(uid)
        tender = str(payment.get('tender_type_id'))
        if tender not in TENDERS or payment.get('payment_name') != TENDERS[tender]:
            raise ValueError('Unsupported or inconsistent tender')
        value = amount(payment.get('amount_tendered'))
        if value * sign <= 0:
            raise ValueError('Payment direction must match sale direction')
        if payment.get('pay_txn_sale_num') not in (None, '', sale_id):
            raise ValueError('Payment belongs to a different sale')
        if not refund and payment.get('return_source_payment_uid'):
            raise ValueError('Original payment linkage is only allowed on linked refunds')
        if refund and not payment.get('return_source_payment_uid'):
            raise ValueError('Refund payment must identify the original tender event')
        paid += value
        if tender == '1':
            cash += value
    if total != amount(payload.get('fiscal_final_total')) or total != amount(payload.get('sales_total')) or paid != total:
        raise ValueError('Sale, line and payment totals must match exactly')
    change = amount(payload.get('sales_change', 0))
    if change < 0 or (refund and change) or (change and cash <= 0):
        raise ValueError('Invalid cash change')
    for key, expected in (('sales_subtotal', subtotal), ('sales_tax', tax), ('sales_discount_amount', discount)):
        if key in payload and amount(payload[key]) != expected:
            raise ValueError(f'{key} differs from recorded line totals')
    snapshot = payload.get('receipt_snapshot')
    required = {'sale_number', 'date', 'time', 'cashier', 'items', 'payments', 'subtotal', 'tax', 'total', 'discount', 'change', 'cash_tendered', 'tax_rows', 'fiscal_context'}
    if not isinstance(snapshot, dict) or required - snapshot.keys():
        raise ValueError('Complete immutable receipt snapshot is required')
    for printed, recorded in (('sale_number', 'sales_num'), ('date', 'sales_date'), ('time', 'sales_time'), ('cashier', 'sales_cashier')):
        if snapshot[printed] != payload[recorded]:
            raise ValueError(f'Printed {printed} differs from recorded sale')
    if not isinstance(snapshot['items'], list) or Counter(_project(row, LINE_KEYS + (('return_source_line_uid',) if refund else ())) for row in snapshot['items']) != Counter(_project(row, LINE_KEYS + (('return_source_line_uid',) if refund else ())) for row in items):
        raise ValueError('Printed items differ from recorded items')
    if not isinstance(snapshot['payments'], list) or Counter(_project(row, PAYMENT_KEYS + (('return_source_payment_uid',) if refund else ())) for row in snapshot['payments']) != Counter(_project(row, PAYMENT_KEYS + (('return_source_payment_uid',) if refund else ())) for row in payments):
        raise ValueError('Printed payments differ from recorded payments')
    for key, value in (('subtotal', subtotal), ('tax', tax), ('total', total), ('discount', discount), ('change', change), ('cash_tendered', max(cash, money(0)) + change)):
        if amount(snapshot[key]) != value:
            raise ValueError(f'Printed {key} differs from recorded sale')
    if not isinstance(snapshot['tax_rows'], list):
        raise ValueError('Printed VAT groups must be a list')
    printed_groups = Counter(tuple(amount(row.get(key)) for key in ('rate', 'net', 'tax', 'gross')) for row in snapshot['tax_rows'])
    if printed_groups != Counter((rate, *values) for rate, values in groups.items()):
        raise ValueError('Printed VAT groups differ from recorded items')
    if str(snapshot.get('document_kind', '')).lower().replace('-', '').replace('_', '').replace(' ', '') in ('prebill', 'quotation'):
        raise ValueError('Pre-bill snapshots cannot be posted as completed sales')
    if snapshot.get('manual_receipt_reference', '') != payload.get('manual_receipt_reference', ''):
        raise ValueError('Printed manual reference differs from sale linkage')
    if not isinstance(snapshot['fiscal_context'], dict):
        raise ValueError('Printed fiscal context must be an object')
    return {'subtotal': subtotal, 'tax': tax, 'total': total, 'discount': discount}
