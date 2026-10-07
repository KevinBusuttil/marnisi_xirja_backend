"""Protocol 2 financial validation. Internal signed amounts never depend on labels."""
from collections import Counter
from copy import deepcopy
from datetime import date, time
from decimal import Decimal
import re

from xirja_marnisi.fiscal.core import amount, digest, identity, money, number
from xirja_marnisi.fiscal.tenders import TENDERS
from xirja_marnisi.fiscal.validation import LINE_KEYS, PAYMENT_KEYS, _project, _rows

V2_LINE_KEYS = LINE_KEYS + ('line_kind', 'price_mode', 'description', 'unit_gross', 'discount_type', 'discount_value')


def cart_hash(payload):
    """Bind every cart fact while allowing the actual tender and cash-change result."""
    frozen = deepcopy(payload)
    for key in ('checkout_authorization', 'sale_pay_methods', 'sales_change', 'sales_cash_tendered'):
        frozen.pop(key, None)
    snapshot = frozen.get('receipt_snapshot')
    if isinstance(snapshot, dict):
        for key in ('payments', 'change', 'cash_tendered'):
            snapshot.pop(key, None)
    return digest(frozen)


def _discount(base, kind, value):
    value = number(value)
    if kind not in ('none', 'amount', 'percent') or value < 0:
        raise ValueError('Invalid discount input')
    if kind == 'none':
        if value:
            raise ValueError('No discount must have zero value')
        return money(0)
    if kind == 'percent':
        if value > 100:
            raise ValueError('Percentage discount cannot exceed 100')
        result = money(base * value / 100)
    else:
        result = amount(value)
    if result > base:
        raise ValueError('Discount exceeds the purchase value')
    return result


def validate_sale_v2(payload, *, preflight=False):
    if not isinstance(payload, dict) or type(payload.get('fiscal_protocol')) is not int or payload['fiscal_protocol'] != 2:
        raise ValueError('Fiscal protocol 2 is required')
    sale_id = identity(payload.get('sales_num'), 'sale')
    date.fromisoformat(str(payload.get('sales_date', '')))
    time.fromisoformat(str(payload.get('sales_time', '')))
    if not isinstance(payload.get('sales_cashier'), str) or not payload['sales_cashier'].strip():
        raise ValueError('Cashier identity is required')
    if any(payload.get(key) for key in ('pre_bill', 'is_prebill', 'return_source_sale_num', 'loy_cust_card_num', 'loy_points_used', 'manual_receipt_reference')) or payload.get('document_type') == 'Pre-bill':
        raise ValueError('Protocol 2 checkout cannot contain pre-bills, linked refunds, loyalty or manual references')
    items = _rows(payload, 'items')
    payments = payload.get('sale_pay_methods')
    if not isinstance(payments, list) or len(payments) > 500 or any(not isinstance(row, dict) for row in payments):
        raise ValueError('Invalid payment rows')
    settings = payload.get('discount_settings')
    if not isinstance(settings, dict) or set(settings) != {'mode', 'type', 'value'} or settings['mode'] not in ('none', 'cart', 'items'):
        raise ValueError('Explicit discount settings are required')
    if settings['mode'] != 'cart' and (settings['type'] not in ('none', 'amount', 'percent') or number(settings['value'])):
        raise ValueError('Only cart discount mode accepts a basket discount input')
    purchase_base = sum((amount(row.get('unit_gross')) * abs(number(row.get('si_qty'))) for row in items if row.get('line_kind') == 'purchase'), money(0))
    cart_discount = _discount(purchase_base, settings['type'], settings['value']) if settings['mode'] == 'cart' else money(0)
    bases = [amount(row.get('unit_gross')) * abs(number(row.get('si_qty'))) if row.get('line_kind') == 'purchase' else money(0) for row in items]
    allocated_discounts = [money(0) for _ in items]
    if purchase_base and cart_discount:
        cents = int(cart_discount * 100)
        raw = [Decimal(cents) * base / purchase_base for base in bases]
        floors = [int(value) for value in raw]
        remainder = cents - sum(floors)
        ranks = sorted(range(len(items)), key=lambda i: (-(raw[i] - floors[i]), i))
        for i in ranks[:remainder]: floors[i] += 1
        allocated_discounts = [money(Decimal(value) / 100) for value in floors]
    subtotal = tax = total = discount = money(0)
    ids, groups = set(), {}
    has_returns = False
    for line_index, line in enumerate(items):
        uid = identity(line.get('line_uid'), 'line')
        if uid in ids:
            raise ValueError('Duplicate line identity')
        ids.add(uid)
        if not line.get('si_id') or not str(line.get('si_name', '')).strip():
            raise ValueError('Item identity and name are required')
        if line.get('si_sale_num') not in (None, '', sale_id) or line.get('return_source_line_uid'):
            raise ValueError('Direct cart items cannot reference another sale or original line')
        kind, price_mode = line.get('line_kind'), line.get('price_mode')
        if kind not in ('purchase', 'return') or price_mode not in ('fixed', 'open'):
            raise ValueError('Explicit line direction and pricing mode are required')
        description = line.get('description')
        if not isinstance(description, str) or len(description) > 500 or any(ord(c) < 32 for c in description) or re.search(r'<[^>]*>', description):
            raise ValueError('Description must be plain text of at most 500 characters')
        if price_mode == 'fixed' and description:
            raise ValueError('Descriptions are supported only for variable-price items')
        qty, rate, price = number(line.get('si_qty')), number(line.get('si_tax_pct')), number(line.get('si_price'))
        unit = amount(line.get('unit_gross'))
        sign = -1 if kind == 'return' else 1
        has_returns |= kind == 'return'
        if qty * sign <= 0 or qty != qty.to_integral_value() or price < 0 or not 0 <= rate <= 100 or unit < 0 or (price_mode == 'open' and unit <= 0):
            raise ValueError('Invalid quantity, unit price or VAT')
        if money(price * (1 + rate / 100)) != unit:
            raise ValueError('Net unit price and inclusive unit price differ')
        base = unit * abs(qty)
        if kind == 'return' or settings['mode'] != 'items':
            if line.get('discount_type') != 'none' or number(line.get('discount_value')):
                raise ValueError('Returns and cart-discount lines cannot have item discount inputs')
            expected_discount = money(0)
            if kind == 'purchase' and settings['mode'] == 'cart':
                expected_discount = allocated_discounts[line_index]
        else:
            expected_discount = _discount(base, line.get('discount_type'), line.get('discount_value'))
        net, vat, gross, reduction = (amount(line.get(key)) for key in ('si_subtotal', 'si_tax', 'si_total', 'si_discount_amount'))
        expected_gross = sign * (base - expected_discount)
        if reduction != expected_discount or gross != expected_gross or net != money(expected_gross / (1 + rate / 100)) or vat != gross - net:
            raise ValueError('Line values disagree with signed inclusive price, discount or VAT')
        group = groups.setdefault((kind, rate), [money(0), money(0), money(0)])
        for i, value in enumerate((net, vat, gross)):
            group[i] += value
        subtotal += net; tax += vat; total += gross; discount += reduction
    settlement = payload.get('settlement')
    direction = 'payment' if total > 0 else 'cash_refund' if total < 0 else 'none'
    if not isinstance(settlement, dict) or set(settlement) != {'direction', 'amount', 'payout_uid'} or settlement['direction'] != direction or amount(settlement['amount']) != abs(total):
        raise ValueError('Settlement differs from the purchase/return balance')
    if total < 0:
        identity(settlement['payout_uid'], 'cash payout')
    elif settlement['payout_uid']:
        raise ValueError('Only cash refunds can identify a payout')
    payment_ids, paid, cash = set(), money(0), money(0)
    for payment in payments:
        uid = identity(payment.get('payment_uid'), 'payment')
        if uid in payment_ids:
            raise ValueError('Duplicate payment identity')
        payment_ids.add(uid)
        tender = str(payment.get('tender_type_id'))
        value = amount(payment.get('amount_tendered'))
        if tender not in TENDERS or payment.get('payment_name') != TENDERS[tender] or value <= 0 or total <= 0:
            raise ValueError('Only positive customer collections use tender rows; outgoing refunds are cash-only')
        if payment.get('pay_txn_sale_num') not in (None, '', sale_id) or payment.get('return_source_payment_uid'):
            raise ValueError('Invalid payment linkage')
        paid += value
        if tender == '1': cash += value
    if (not preflight or payments) and paid != max(total, money(0)):
        raise ValueError('Customer collections must match the positive balance exactly')
    change = amount(payload.get('sales_change', 0))
    if change < 0 or (change and cash <= 0):
        raise ValueError('Cash change requires an incoming cash collection')
    for key, expected in (('fiscal_final_total', total), ('sales_total', total), ('sales_subtotal', subtotal), ('sales_tax', tax), ('sales_discount_amount', discount)):
        if key not in payload or amount(payload[key]) != expected:
            raise ValueError(f'{key} differs from recorded line totals')
    if 'sales_cash_tendered' in payload and amount(payload['sales_cash_tendered']) != cash + change:
        raise ValueError('Cash tendered differs from incoming cash and change')
    snapshot = payload.get('receipt_snapshot')
    if not isinstance(snapshot, dict):
        raise ValueError('Immutable receipt snapshot is required')
    for printed, recorded in (('sale_number', 'sales_num'), ('date', 'sales_date'), ('time', 'sales_time'), ('cashier', 'sales_cashier')):
        if snapshot.get(printed) != payload[recorded]:
            raise ValueError(f'Printed {printed} differs from recorded sale')
    for key, rows, keys in (('items', items, V2_LINE_KEYS), ('payments', payments, PAYMENT_KEYS)):
        printed = snapshot.get(key)
        if not isinstance(printed, list) or Counter(_project(row, keys) for row in printed) != Counter(_project(row, keys) for row in rows):
            raise ValueError(f'Printed {key} differ from recorded rows')
    for key, expected in (('subtotal', subtotal), ('tax', tax), ('total', total), ('discount', discount), ('change', change), ('cash_tendered', cash + change)):
        if amount(snapshot.get(key)) != expected:
            raise ValueError(f'Printed {key} differs from recorded totals')
    if snapshot.get('discount_settings') != settings or snapshot.get('settlement') != settlement:
        raise ValueError('Printed discount inputs or settlement differ from recorded values')
    printed_groups = snapshot.get('tax_rows')
    if not isinstance(printed_groups, list) or any(not isinstance(row, dict) for row in printed_groups):
        raise ValueError('Printed VAT groups must be a list')
    if Counter((row.get('line_kind'), *(amount(row.get(key)) for key in ('rate', 'net', 'tax', 'gross'))) for row in printed_groups) != Counter((kind, rate, *values) for (kind, rate), values in groups.items()):
        raise ValueError('Printed purchase/return VAT groups differ from recorded lines')
    if not isinstance(snapshot.get('fiscal_context'), dict) or snapshot.get('manual_receipt_reference') or snapshot.get('return_source_sale_num') or str(snapshot.get('document_kind', '')).lower().replace('-', '').replace('_', '').replace(' ', '') in ('prebill', 'quotation'):
        raise ValueError('Invalid completed receipt context or document kind')
    return {'subtotal': subtotal, 'tax': tax, 'total': total, 'discount': discount, 'has_returns': has_returns}
