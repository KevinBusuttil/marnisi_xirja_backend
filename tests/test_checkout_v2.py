"""Cash-return arithmetic, immutable cart authorization and payout recovery."""
from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import pytest

from xirja_marnisi.fiscal import checkout_v2 as v2, service
from xirja_marnisi.fiscal.core import digest, money, number
from xirja_marnisi.fiscal.validation import validate_sale
from xirja_marnisi.fiscal.validation_v2 import cart_hash, validate_sale_v2
from xirja_marnisi.fiscal.tenders import TENDERS
from test_fiscal_service import MemoryDB


def sample_v2(purchase=70, returned=50, *, tender='1', rate=18):
    lines = []
    for kind, value in (('purchase', purchase), ('return', returned)):
        if not value:
            continue
        gross = money(value) * (-1 if kind == 'return' else 1)
        net = money(gross / (1 + number(rate) / 100))
        lines.append({'line_uid': 'line-' + kind, 'si_id': service.VINEYARD + '::' + kind,
            'si_name': kind.title(), 'si_qty': -1 if kind == 'return' else 1,
            'si_price': float(money(value) / (1 + number(rate) / 100)), 'si_tax_pct': rate,
            'si_subtotal': float(net), 'si_tax': float(gross - net), 'si_total': float(gross),
            'si_discount_amount': 0, 'line_kind': kind, 'price_mode': 'fixed', 'description': '',
            'unit_gross': value, 'discount_type': 'none', 'discount_value': 0})
    settings = {'mode': 'none', 'type': 'none', 'value': 0}
    sale = {'fiscal_protocol': 2, 'sales_num': 'sale-v2', 'sales_date': '2026-10-02', 'sales_time': '12:00:00',
        'sales_cashier': 'cashier@example.invalid', 'sales_store': service.VINEYARD,
        'sales_registerId': service.REGISTER, 'items': lines, 'discount_settings': settings,
        'sale_pay_methods': [], 'sales_change': 0, 'receipt_snapshot': {'fiscal_context': {'mode': 'Test'}}}
    rebuild(sale, tender=tender)
    return sale


def rebuild(sale, tender='1'):
    """Fixture-only reconstruction; tests mutate afterward to exercise validators."""
    lines = sale['items']
    subtotal = sum((number(row['si_subtotal']) for row in lines), Decimal(0))
    tax = sum((number(row['si_tax']) for row in lines), Decimal(0))
    total = money(subtotal + tax)
    discount = sum((number(row['si_discount_amount']) for row in lines), Decimal(0))
    sale.update(sales_subtotal=float(subtotal), sales_tax=float(tax), sales_total=float(total),
        fiscal_final_total=float(total), sales_discount_amount=float(discount), sales_cash_tendered=0)
    sale['settlement'] = {'direction': 'payment' if total > 0 else 'cash_refund' if total < 0 else 'none',
        'amount': float(abs(total)), 'payout_uid': 'payout-v2' if total < 0 else ''}
    sale['sale_pay_methods'] = [{'payment_uid': 'payment-v2', 'tender_type_id': tender,
        'payment_name': TENDERS[tender], 'amount_tendered': float(total)}] if total > 0 else []
    cash = float(total) if total > 0 and tender == '1' else 0
    sale['sales_cash_tendered'] = cash + sale['sales_change']
    groups = {}
    for row in lines:
        key = (row['line_kind'], row['si_tax_pct'])
        values = groups.setdefault(key, [Decimal(0), Decimal(0), Decimal(0)])
        for i, field in enumerate(('si_subtotal', 'si_tax', 'si_total')): values[i] += number(row[field])
    context = sale['receipt_snapshot']['fiscal_context']
    sale['receipt_snapshot'] = {'fiscal_protocol': 2, 'sale_number': sale['sales_num'], 'date': sale['sales_date'],
        'time': sale['sales_time'], 'cashier': sale['sales_cashier'], 'items': deepcopy(lines),
        'payments': deepcopy(sale['sale_pay_methods']), 'subtotal': float(subtotal), 'tax': float(tax),
        'total': float(total), 'discount': float(discount), 'change': sale['sales_change'],
        'cash_tendered': sale['sales_cash_tendered'], 'discount_settings': deepcopy(sale['discount_settings']),
        'settlement': deepcopy(sale['settlement']), 'fiscal_context': context,
        'tax_rows': [{'line_kind': kind, 'rate': rate, 'net': float(vals[0]), 'tax': float(vals[1]), 'gross': float(vals[2])} for (kind, rate), vals in groups.items()]}


@pytest.mark.parametrize('purchases,returns,balance', [(0,50,-50),(70,50,20),(30,50,-20),(50,50,0),(70,0,70)])
def test_required_customer_examples_have_signed_balances_and_explicit_settlement(purchases, returns, balance):
    payload = sample_v2(purchases, returns)
    assert validate_sale(payload)['total'] == money(balance)
    assert payload['settlement']['amount'] == abs(balance)
    if balance <= 0: assert payload['sale_pay_methods'] == []


@pytest.mark.parametrize('tender', TENDERS)
def test_each_backend_tender_can_collect_exchange_balance(tender):
    assert validate_sale(sample_v2(tender=tender))['total'] == money(20)


@pytest.mark.parametrize('mutate', [
    lambda p: p['items'][0].update(si_qty=-1),
    lambda p: p['items'][1].update(line_kind='purchase'),
    lambda p: p['items'][0].update(si_tax_pct=20),
    lambda p: p['items'][0].update(description='unconfigured'),
    lambda p: p['items'][0].update(discount_value=1),
    lambda p: p['items'][1].update(si_discount_amount=1),
    lambda p: p['items'][0].update(unit_gross=70.001),
    lambda p: p['items'][0].update(price_mode='forged'),
    lambda p: p['receipt_snapshot']['items'][0].update(description='hidden'),
    lambda p: p['receipt_snapshot']['tax_rows'][1].update(line_kind='purchase'),
    lambda p: p['settlement'].update(direction='cash_refund'),
    lambda p: p['settlement'].update(amount=19.99),
    lambda p: p['settlement'].update(payout_uid='unexpected'),
    lambda p: p.update(sales_cash_tendered=50),
    lambda p: p.update(return_source_sale_num='historical'),
    lambda p: p['sale_pay_methods'][0].update(amount_tendered=-20),
    lambda p: p['receipt_snapshot'].update(document_kind='pre_bill'),
    lambda p: p['receipt_snapshot'].update(discount_settings={'mode':'cart','type':'percent','value':50}),
])
def test_v2_rejects_forged_financial_or_printed_evidence(mutate):
    sale = sample_v2(); mutate(sale)
    with pytest.raises(ValueError): validate_sale(sale)


def test_cash_refund_rejects_card_or_fake_outgoing_tender_rows():
    sale = sample_v2(0, 50)
    sale['sale_pay_methods'] = [{'payment_uid':'pay','tender_type_id':'7','payment_name':'Card BOV','amount_tendered':-50}]
    with pytest.raises(ValueError, match='cash-only'): validate_sale(sale)


def test_preflight_accepts_empty_positive_tenders_but_finalization_does_not():
    sale = sample_v2()
    sale['sale_pay_methods'] = []; sale['sales_cash_tendered'] = 0
    sale['receipt_snapshot'].update(payments=[], cash_tendered=0)
    assert validate_sale_v2(sale, preflight=True)['total'] == money(20)
    with pytest.raises(ValueError, match='collections'): validate_sale(sale)


def test_cart_binding_ignores_only_tender_change_and_authorization():
    sale = sample_v2(); changed = deepcopy(sale)
    changed['sale_pay_methods'] = []; changed['sales_change'] = 1; changed['sales_cash_tendered'] = 0
    changed['receipt_snapshot'].update(payments=[], change=1, cash_tendered=0)
    changed['checkout_authorization'] = 'opaque'
    assert cart_hash(sale) == cart_hash(changed)
    for path in ('sales_total','sales_cashier','sales_date','settlement','discount_settings'):
        tampered = deepcopy(sale); tampered[path] = 'changed'
        assert cart_hash(sale) != cart_hash(tampered)


def test_full_item_discount_completes_without_payment():
    sale = sample_v2(70,0); sale['discount_settings'] = {'mode':'items','type':'none','value':0}
    sale['items'][0].update(discount_type='percent',discount_value=100,si_discount_amount=70,si_total=0,si_subtotal=0,si_tax=0)
    rebuild(sale)
    assert validate_sale(sale)['total'] == 0
    assert sale['settlement']['direction'] == 'none'


@pytest.mark.parametrize('kind,value,discount',[('percent',10,7),('amount',7,7)])
def test_item_euro_and_percentage_inputs_validate(kind,value,discount):
    sale=sample_v2(70,0); sale['discount_settings']={'mode':'items','type':'none','value':0}
    net=money(63/Decimal('1.18'))
    sale['items'][0].update(discount_type=kind,discount_value=value,si_discount_amount=discount,si_total=63,si_subtotal=float(net),si_tax=float(63-net))
    rebuild(sale); assert validate_sale(sale)['total']==money(63)


def test_largest_remainder_cart_discount_preserves_cent_and_excludes_returns():
    sale=sample_v2(1,1,rate=0)
    second=deepcopy(sale['items'][0]);second.update(line_uid='line-second')
    third=deepcopy(second);third.update(line_uid='line-third')
    sale['items']=[sale['items'][0],sale['items'][1],second,third]
    sale['discount_settings']={'mode':'cart','type':'amount','value':.02}
    for row, discount in zip(sale['items'],[.01,0,.01,0]):
        row.update(si_discount_amount=discount,si_total=float(money(row['si_total'])-money(discount)),si_subtotal=float(money(row['si_subtotal'])-money(discount)))
    rebuild(sale)
    assert validate_sale(sale)['total']==money('1.98')
    sale['items'][2]['si_discount_amount']=0
    with pytest.raises(ValueError):validate_sale(sale)


@pytest.mark.parametrize('description',['Meal with grapes','', 'x'*500])
def test_open_price_optional_description_and_vat(description):
    sale=sample_v2(70,0);sale['items'][0].update(price_mode='open',description=description)
    rebuild(sale); assert validate_sale(sale)['total']==70


@pytest.mark.parametrize('description',['x'*501,'<script>alert(1)</script>','line\nfeed','\x1bprinter-command'])
def test_description_rejects_excess_markup_and_control_codes(description):
    sale=sample_v2(70,0);sale['items'][0].update(price_mode='open',description=description)
    rebuild(sale)
    with pytest.raises(ValueError,match='plain text'):validate_sale(sale)


class V2DB(MemoryDB):
    def __init__(self):
        super().__init__()
        for table in ('Marnisi Checkout Authorization','Marnisi Cash Payout','Marnisi Cash Payout Event'):
            self.tables[table]=[]
        self.catalogue=[]
    def sql(self, query, values=(), as_dict=False):
        text=' '.join(query.split())
        if 'FROM `tabVineyard Item`' in text:return deepcopy(self.catalogue)
        if 'FROM `tabMarnisi Checkout Authorization`' in text:
            return [deepcopy(row) for row in self.tables['Marnisi Checkout Authorization'] if row['sale_num']==values[0]]
        if 'FROM `tabMarnisi Cash Payout`' in text:
            return [deepcopy(row) for row in self.tables['Marnisi Cash Payout'] if row['sale_num']==values[0] and row['payout_uid']==values[1]]
        if text.startswith('UPDATE `tabMarnisi Cash Payout`'):
            for row in self.tables['Marnisi Cash Payout']:
                if row['payout_uid']==values[2]:row.update(state=values[0],handover_uid=values[1])
            return []
        if text.startswith('INSERT INTO `tabMarnisi Checkout Authorization`') or text.startswith('INSERT INTO `tabMarnisi Cash Payout`'):
            table=text.split('`')[1][3:]
            if table==self.failed_table:raise RuntimeError('late write failed')
            fields=[part.strip(' `') for part in text.split('(',1)[1].split(')',1)[0].split(',')]
            row=dict(zip(fields,values,strict=True))
            if any(existing['sale_num']==row['sale_num'] for existing in self.tables[table]):raise RuntimeError('duplicate')
            self.tables[table].append(row);return []
        return super().sql(query,values,as_dict)


@pytest.fixture
def db(monkeypatch):
    db=V2DB()
    monkeypatch.setattr(service,'frappe',SimpleNamespace(db=db,utils=SimpleNamespace(now=lambda:'2026-10-02 12:00:00'),PermissionError=PermissionError,AuthenticationError=PermissionError))
    monkeypatch.setattr(service,'require_authenticated_user',lambda:'cashier@example.invalid')
    monkeypatch.setattr(service,'require_pos_access',lambda *a,**k:{'user':'cashier@example.invalid'})
    return db


def configured(db,purchase=30,returned=50):
    sale=sample_v2(purchase,returned)
    sale['receipt_snapshot']['fiscal_context']=service.public_context(db.settings)
    db.catalogue=[{'item_code':row['line_kind'],'item_name':row['si_name'],'sell_price':row['si_price'],
        'vat_rate':18,'is_enabled':1,'is_returnable':1,'allow_variable_price':0} for row in sale['items']]
    return sale


def authorize(sale):
    response=v2.prepare_checkout(sale)
    sale['checkout_authorization']=response['authorization']
    return response


def ingest(sale):return service.ingest_batch({'sales':[sale]})['confirmations'][0]


def test_authorization_is_stable_frozen_and_cashier_bound(db):
    sale=configured(db);first=authorize(sale)
    assert authorize(sale)==first
    sale['sales_time']='13:00:00';sale['receipt_snapshot']['time']='13:00:00'
    with pytest.raises(ValueError,match='different cart'):authorize(sale)
    assert len(db.tables['Marnisi Checkout Authorization'])==1


def test_return_requires_authorization_and_rechecks_current_eligibility_at_prepare(db):
    sale=configured(db)
    assert ingest(sale)['status']=='error'
    db.catalogue[1]['is_returnable']=0
    with pytest.raises(ValueError,match='returnable'):authorize(sale)


def test_authorized_catalogue_evidence_survives_changes_after_payment(db):
    sale=configured(db,70,50);authorize(sale)
    db.catalogue[1]['is_returnable']=0;db.catalogue[0]['sell_price']=900
    ack=ingest(sale)
    assert ack['fiscal_status']=='Test',ack
    assert len(ack['fiscal_receipts'])==1
    assert ack['fiscal_receipts'][0]['record_kind']=='Transaction'


@pytest.mark.parametrize('purchase,returned',[(0,50),(30,50),(50,50),(70,50),(70,0)])
def test_transaction_receipt_once_for_each_settlement_and_retry(db,purchase,returned):
    sale=configured(db,purchase,returned)
    if returned:authorize(sale)
    ack=ingest(sale)
    assert ack['status']=='synchronized',ack
    assert len(ack['fiscal_receipts'])==1
    assert ack['fiscal_receipts'][0]['payment_uid'] is None
    assert ack['fiscal_receipts'][0]['payment_amount']==purchase-returned
    saved=deepcopy(db.tables)
    assert ingest(sale)==ack
    assert saved==db.tables
    assert len(db.tables['Marnisi Cash Payout'])==int(returned>purchase)


def test_payout_begin_confirm_recovery_is_idempotent_and_ack_stays_frozen(db):
    sale=configured(db);authorize(sale);ack=ingest(sale)
    args={'sale_num':sale['sales_num'],'payout_uid':'payout-v2','handover_uid':'handover-1'}
    assert v2.payout_action(args,'status')['cash_payout']['state']=='pending'
    with pytest.raises(ValueError,match='Begin'):v2.payout_action(args,'confirm')
    first=v2.payout_action(args,'begin'); assert first['cash_payout']['state']=='started'
    assert v2.payout_action(args,'begin')==first
    with pytest.raises(ValueError,match='already started'):v2.payout_action({**args,'handover_uid':'another'},'begin')
    done=v2.payout_action(args,'confirm');assert done['cash_payout']['state']=='confirmed'
    assert v2.payout_action(args,'confirm')==done
    assert v2.payout_action(args,'begin')==done
    assert len(db.tables['Marnisi Cash Payout Event'])==2
    assert ingest(sale)==ack
    assert ack['cash_payout']['state']=='pending'


def test_review_cash_refund_cannot_authorize_handover(db):
    sale=configured(db);authorize(sale);db.settings['legal_name']='Changed'
    ack=ingest(sale);assert ack['fiscal_status']=='Review'
    with pytest.raises(ValueError,match='Review'):v2.payout_action({'sale_num':sale['sales_num'],'payout_uid':'payout-v2','handover_uid':'handover'},'begin')


@pytest.mark.parametrize('table',['Marnisi POS Sale Item','Marnisi Fiscal Receipt','Marnisi Cash Payout'])
def test_late_failure_rolls_back_sale_receipt_and_payout(db,table):
    sale=configured(db);authorize(sale);db.failed_table=table
    assert ingest(sale)['status']=='error'
    assert not db.tables['Marnisi POS Sale']
    assert not db.tables['Marnisi Fiscal Receipt']
    assert not db.tables['Marnisi Cash Payout']
    assert not db.sequence
    assert len(db.tables['Marnisi Checkout Authorization'])==1


def test_open_price_requires_server_permission_and_correct_tax(db):
    sale=configured(db,70,0);sale['items'][0].update(price_mode='open',description='Meal');rebuild(sale)
    assert ingest(sale)['status']=='error'
    db.catalogue[0]['allow_variable_price']=1
    assert ingest(sale)['fiscal_status']=='Test'


def test_staff_permission_used_for_direct_return_and_original_cashier_required(db,monkeypatch):
    calls=[]
    monkeypatch.setattr(service,'require_pos_access',lambda *a,**k:calls.append(k) or {'user':'cashier@example.invalid'})
    sale=configured(db);authorize(sale);assert ingest(sale)['status']=='synchronized'
    assert all(row['staff_allowed'] for row in calls)
    monkeypatch.setattr(service,'require_authenticated_user',lambda:'other@example.invalid')
    with pytest.raises(ValueError,match='original cashier'):v2.payout_action({'sale_num':sale['sales_num'],'payout_uid':'payout-v2'},'status')


@pytest.mark.parametrize('field,value',[('is_enabled',0),('item_name','Changed'),('vat_rate',5),('sell_price',999)])
def test_stale_catalogue_refuses_preflight_before_money_taken(db,field,value):
    sale=configured(db);db.catalogue[0][field]=value
    with pytest.raises(ValueError):authorize(sale)
    assert db.tables['Marnisi Checkout Authorization']==[]


def test_payout_uid_is_bound_to_cashier_and_sale(db):
    sale=configured(db);authorize(sale);ingest(sale)
    with pytest.raises(ValueError,match='Unknown'):v2.payout_action({'sale_num':'other-sale','payout_uid':'payout-v2'},'status')


def test_fiscal_mode_change_and_recovery_block_new_handover_but_allow_confirming_started_cash(db):
    sale=configured(db);authorize(sale);ingest(sale)
    args={'sale_num':sale['sales_num'],'payout_uid':'payout-v2','handover_uid':'handover'}
    db.settings['issuance_suspended']=1
    with pytest.raises(ValueError,match='recovery'):v2.payout_action(args,'begin')
    db.settings['issuance_suspended']=0
    v2.payout_action(args,'begin')
    db.settings['issuance_suspended']=1
    assert v2.payout_action(args,'confirm')['cash_payout']['state']=='confirmed'


def test_disabled_mode_refund_is_accepted_without_fiscal_number(db):
    db.settings['mode']='Disabled'
    sale=configured(db);authorize(sale);ack=ingest(sale)
    assert ack['fiscal_status']=='Disabled'
    assert ack['fiscal_receipts']==[]
    assert v2.payout_action({'sale_num':sale['sales_num'],'payout_uid':'payout-v2','handover_uid':'handover'},'begin')['cash_payout']['state']=='started'


def test_duplicate_payout_event_retry_does_not_allocate_new_sequence(db):
    sale=configured(db);authorize(sale);ingest(sale)
    before=deepcopy(db.sequence)
    args={'sale_num':sale['sales_num'],'payout_uid':'payout-v2','handover_uid':'handover'}
    for action in ('begin','begin','confirm','confirm','status'):v2.payout_action(args,action)
    assert db.sequence==before


def test_v2_cannot_enter_v1_manual_reservation_and_duplicate_electronic_evidence(db):
    sale=configured(db,70,0)
    with pytest.raises(ValueError,match='protocol-1'):
        service.reserve_manual_receipt(sale,'PAPER-V2','Unsafe cross-protocol paper recovery')
    assert db.tables['Marnisi Manual Receipt Link']==[]
    assert ingest(sale)['fiscal_status']=='Test'
