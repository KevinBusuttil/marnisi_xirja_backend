"""Authenticated Marnisi fiscal exception/reconciliation routes."""
import frappe
from xirja_marnisi.api.security import parse_args
from xirja_marnisi.fiscal import service


@frappe.whitelist()
def get_refund_context(args=''):
    return service.refund_context(str(parse_args(args).get('sale_num') or ''))


@frappe.whitelist(methods=['POST'])
def submit_linked_refund(args=''):
    return service.ingest_batch(parse_args(args), refund=True)


@frappe.whitelist(methods=['POST'])
def link_manual_receipt(args=''):
    payload = parse_args(args)
    return service.link_manual(payload.get('sale_num'), payload.get('manual_receipt_reference'), payload.get('reason'))


@frappe.whitelist()
def get_reconciliation(args=''):
    return {'status': 'success', 'rows': service.reconciliation_rows(parse_args(args))}


@frappe.whitelist(methods=['POST'])
def suspend_issuance(args=''):
    return service.suspend_for_recovery(parse_args(args).get('reference'))


@frappe.whitelist(methods=['POST'])
def resume_issuance(args=''):
    payload = parse_args(args)
    return service.resume_after_reconciliation(payload.get('reference'), payload.get('high_watermarks'))


@frappe.whitelist(methods=['POST'])
def reserve_manual_receipt(args=''):
    payload = parse_args(args)
    return service.reserve_manual_receipt(payload.get('sale'), payload.get('manual_receipt_reference'), payload.get('reason'))


@frappe.whitelist(methods=['POST'])
def prepare_checkout(args=''):
    from xirja_marnisi.fiscal.checkout_v2 import prepare_checkout as prepare
    return prepare(parse_args(args).get('sale'))


@frappe.whitelist(methods=['POST'])
def begin_cash_payout(args=''):
    from xirja_marnisi.fiscal.checkout_v2 import payout_action
    return payout_action(parse_args(args), 'begin')


@frappe.whitelist(methods=['POST'])
def confirm_cash_payout(args=''):
    from xirja_marnisi.fiscal.checkout_v2 import payout_action
    return payout_action(parse_args(args), 'confirm')


@frappe.whitelist()
def get_cash_payout_status(args=''):
    from xirja_marnisi.fiscal.checkout_v2 import payout_action
    return payout_action(parse_args(args), 'status')
