"""Deterministic money, payload identity and refund allocation.

Adapted from Xirja 24c38fa fiscal/core.py. The Marnisi pricing contract rounds
VAT-inclusive unit price once and retains the original VAT-exclusive catalogue price.
"""
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal('0.01')


def number(value):
    if isinstance(value, bool) or value is None or value == '':
        raise ValueError('A finite number is required')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError('A finite number is required') from exc
    if not result.is_finite() or abs(result) > Decimal('9999999999'):
        raise ValueError('Number is non-finite or out of range')
    return result


def money(value=0):
    return number(value).quantize(CENT, rounding=ROUND_HALF_UP)


def amount(value):
    result = number(value)
    if result != money(result):
        raise ValueError('Money must have at most two decimal places')
    return result


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def _canonical(value):
    if isinstance(value, dict):
        return {key: _canonical(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_canonical(val) for val in value]
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        decimal = number(value)
        return format(decimal.normalize(), 'f') if decimal else '0'
    return value


def digest(value):
    return hashlib.sha256(encode(_canonical(value)).encode('utf-8')).hexdigest()


def identity(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}', value):
        raise ValueError(f'Invalid or missing {label} identity')
    return value


def refund_allocation(original_quantity, original_amount, refunded_quantity, refunded_amount, quantity):
    """Cumulative rounding allocates the final residual cent exactly once."""
    total_qty, used_qty, requested = map(number, (original_quantity, refunded_quantity, quantity))
    if total_qty <= 0 or requested <= 0 or used_qty < 0 or used_qty + requested > total_qty:
        raise ValueError('Refund exceeds original quantity')
    allocated = money(amount(original_amount) * (used_qty + requested) / total_qty) - amount(refunded_amount)
    if allocated < 0:
        raise ValueError('Original refund allocations are inconsistent')
    return allocated
