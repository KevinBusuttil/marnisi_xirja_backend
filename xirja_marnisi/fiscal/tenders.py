"""Canonical Marnisi tender identifiers; externally settled electronic payments."""
TENDERS = {'1': 'Cash', '7': 'Card BOV', '12': 'Stripe', '13': 'Bank Transfer'}
PAYMENT_METHODS = [{'payment_type_id': key, 'payment_type_name': value} for key, value in TENDERS.items()]
CAPABILITIES = {'checkout_v2': True, 'cash_returns': True, 'open_price': True, 'discounts': True}
