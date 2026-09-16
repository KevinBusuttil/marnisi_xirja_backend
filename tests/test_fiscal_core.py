from copy import deepcopy
from decimal import Decimal
import pytest

from xirja_marnisi.fiscal.core import amount, digest, money, refund_allocation
from xirja_marnisi.fiscal.validation import validate_sale


def sample_sale():
    line = {'line_uid':'line-1','si_id':"Marnisi M'Xlokk::wine",'si_name':'Wine','si_qty':1,
            'si_price':2.966,'si_tax_pct':18,'si_subtotal':2.97,'si_tax':0.53,'si_total':3.50,'si_discount_amount':0}
    payment = {'payment_uid':'payment-1','tender_type_id':'1','payment_name':'Cash','amount_tendered':3.5}
    snapshot = {'sale_number':'sale-1','date':'2026-09-16','time':'12:00:00','cashier':'cashier@example.invalid',
        'items':[deepcopy(line)],'payments':[deepcopy(payment)],'subtotal':2.97,'tax':0.53,'total':3.5,
        'discount':0,'change':1.5,'cash_tendered':5,'tax_rows':[{'rate':18,'net':2.97,'tax':0.53,'gross':3.5}],
        'fiscal_context':{'mode':'Test'}}
    return {'fiscal_protocol':1,'sales_num':'sale-1','sales_date':'2026-09-16','sales_time':'12:00:00',
        'sales_cashier':'cashier@example.invalid','sales_store':"Marnisi M'Xlokk",'sales_registerId':"Marnisi M'Xlokk-MAIN",
        'items':[line],'sale_pay_methods':[payment],'sales_total':3.5,'fiscal_final_total':3.5,'sales_change':1.5,'receipt_snapshot':snapshot}


def test_preserves_three_decimal_net_catalogue_contract():
    assert validate_sale(sample_sale())['total'] == Decimal('3.50')


@pytest.mark.parametrize('value',[float('nan'),float('inf'),True,None,'', 'broken'])
def test_malformed_numbers_rejected(value):
    with pytest.raises(ValueError):
        money(value)


def test_money_rounds_half_up_but_recorded_amount_requires_cents():
    assert money('0.005') == Decimal('0.01')
    with pytest.raises(ValueError):
        amount('0.005')


def test_fingerprint_is_order_independent_and_numeric_runtime_independent():
    assert digest({'a':1,'b':[1.5,True]}) == digest({'b':[1.50,True],'a':1.0})
    assert digest({'a':1}) != digest({'a':2})


@pytest.mark.parametrize('mutate',[
    lambda p:p.update(fiscal_protocol=0),
    lambda p:p['items'][0].update(si_qty=-1),
    lambda p:p['items'][0].update(si_tax=0),
    lambda p:p.update(fiscal_final_total=3.49),
    lambda p:p['sale_pay_methods'][0].update(amount_tendered=4),
    lambda p:p['sale_pay_methods'][0].update(tender_type_id='4'),
    lambda p:p['receipt_snapshot']['items'][0].update(si_name='Forged'),
    lambda p:p['receipt_snapshot']['tax_rows'][0].update(net=3.5),
    lambda p:p['receipt_snapshot'].update(total=3.49),
    lambda p:p['receipt_snapshot'].update(cash_tendered=3.5),
    lambda p:p['receipt_snapshot'].update(cashier='someone else'),
    lambda p:p['receipt_snapshot'].update(document_kind='Pre-bill'),
    lambda p:p.update(loy_points_used=1),
    lambda p:p['receipt_snapshot'].update(manual_receipt_reference='UNLINKED'),
])
def test_tampered_or_unsupported_financial_evidence_rejected(mutate):
    payload=sample_sale();mutate(payload)
    with pytest.raises((ValueError,KeyError)):
        validate_sale(payload)


def test_duplicate_payment_identity_fails_before_persistence():
    payload=sample_sale();payload['sale_pay_methods'].append(deepcopy(payload['sale_pay_methods'][0]))
    with pytest.raises(ValueError,match='Duplicate payment'):
        validate_sale(payload)


def test_split_payment_snapshot_must_match_each_event():
    payload=sample_sale()
    payload['sale_pay_methods'][0]['amount_tendered']=1
    payload['sale_pay_methods'].append({'payment_uid':'payment-2','tender_type_id':'7','payment_name':'Card BOV','amount_tendered':2.5})
    payload['receipt_snapshot']['payments']=deepcopy(payload['sale_pay_methods'])
    payload['sales_change']=0
    payload['receipt_snapshot'].update(change=0,cash_tendered=1)
    assert validate_sale(payload)['total']==Decimal('3.5')
    payload['receipt_snapshot']['payments'][1]['payment_uid']='different'
    with pytest.raises(ValueError,match='Printed payments'):
        validate_sale(payload)


def test_cumulative_refund_rounding_consumes_exact_final_cent():
    first=refund_allocation(3,'1.00',0,0,1)
    second=refund_allocation(3,'1.00',1,first,1)
    final=refund_allocation(3,'1.00',2,first+second,1)
    assert (first,second,final)==(Decimal('.33'),Decimal('.34'),Decimal('.33'))
    assert first+second+final==Decimal('1.00')
    with pytest.raises(ValueError,match='exceeds'):
        refund_allocation(3,'1.00',3,'1.00',1)


@pytest.mark.parametrize('field',['sales_subtotal','sales_tax','sales_discount_amount'])
def test_header_financial_fields_cannot_disagree_with_lines(field):
    payload=sample_sale();payload[field]=99
    with pytest.raises(ValueError,match='recorded line totals'):
        validate_sale(payload)


def test_boolean_protocol_does_not_impersonate_version_one():
    payload=sample_sale();payload['fiscal_protocol']=True
    with pytest.raises(ValueError,match='protocol'):
        validate_sale(payload)


@pytest.mark.parametrize('field,collection',[('return_source_line_uid','items'),('return_source_payment_uid','sale_pay_methods')])
def test_original_linkage_is_not_accepted_on_an_unlinked_positive_sale(field,collection):
    payload=sample_sale();payload[collection][0][field]='original'
    with pytest.raises(ValueError,match='linkage'):
        validate_sale(payload)
