from copy import deepcopy
from decimal import Decimal
from types import SimpleNamespace
import pytest

from xirja_marnisi.fiscal import service
from xirja_marnisi.fiscal.core import digest
from test_fiscal_core import sample_sale


class MemoryDB:
    """A transactional fake for deterministic failure injection; real DB tests are separate."""
    def __init__(self):
        self.tables={name:[] for name in ('Marnisi POS Sale','Marnisi POS Sale Item','Marnisi POS Sale Payment','Marnisi Fiscal Receipt','Marnisi Manual Receipt Link')}
        self.savepoints={};self.sequence={};self.failed_table=None
        self.settings={'name':'settings','vineyard':service.VINEYARD,'mode':'Test','currency':'EUR'}

    def savepoint(self,name):
        self.savepoints[name]=deepcopy((self.tables,self.sequence))

    def rollback(self,save_point):
        self.tables,self.sequence=deepcopy(self.savepoints[save_point])

    def sql(self,query,values=(),as_dict=False):
        text=' '.join(query.split())
        if 'FROM `tabMarnisi Fiscal Settings`' in text:
            return [deepcopy(self.settings)]
        if text.startswith('SELECT name,sale_num'):
            return [deepcopy(row) for row in self.tables['Marnisi POS Sale'] if row['sale_num']==values[0]]
        if 'FROM `tabVineyard Item`' in text:
            return [{'name':'wine','vineyard':service.VINEYARD,'item_code':'wine','item_name':'Wine','sell_price':2.966,'vat_rate':18,'is_enabled':1}]
        if text.startswith('SELECT name,source_sale,source_payment'):
            return [deepcopy(row) for row in self.tables['Marnisi Fiscal Receipt'] if row['source_sale']==values[0]]
        if text.startswith('INSERT IGNORE INTO `tabMarnisi Fiscal Counter`'):
            self.sequence.setdefault(values[0],0);return []
        if text.startswith('SELECT last_number'):
            return [(self.sequence[values[0]],)]
        if text.startswith('UPDATE `tabMarnisi Fiscal Counter`'):
            self.sequence[values[1]]=values[0];return []
        if text.startswith('UPDATE `tabMarnisi POS Sale` SET acknowledgement'):
            for row in self.tables['Marnisi POS Sale']:
                if row['name']==values[1]:row['acknowledgement']=values[0]
            return []
        if text.startswith('INSERT INTO `tab'):
            table=text.split('`')[1][3:]
            if table==self.failed_table:raise RuntimeError('Private database connection error password=secret')
            columns=[part.strip(' `') for part in text.split('(',1)[1].split(')',1)[0].split(',')]
            row=dict(zip(columns,values,strict=True))
            if any(existing['name']==row['name'] for existing in self.tables[table]):raise RuntimeError('duplicate key')
            self.tables[table].append(row);return []
        if text.startswith('SELECT manual_reference,payload_hash FROM'):
            return [row for row in self.tables['Marnisi Manual Receipt Link'] if row['source_sale']==values[0] and row.get('is_reservation')]
        if text.startswith('SELECT name,source_sale,manual_reference'):
            return [row for row in self.tables['Marnisi Manual Receipt Link'] if row['manual_reference']==values[1]]
        raise AssertionError(text)


@pytest.fixture
def database(monkeypatch):
    db=MemoryDB()
    fake=SimpleNamespace(db=db,utils=SimpleNamespace(now=lambda:'2026-09-16 12:00:00'),PermissionError=PermissionError,AuthenticationError=PermissionError)
    monkeypatch.setattr(service,'frappe',fake)
    monkeypatch.setattr(service,'require_authenticated_user',lambda:'cashier@example.invalid')
    monkeypatch.setattr(service,'require_pos_access',lambda *a,**k:{'user':'cashier@example.invalid'})
    return db


def payload(database):
    sale=sample_sale();sale['receipt_snapshot']['fiscal_context']=service.public_context(database.settings)
    return sale


def ingest(sale):
    return service.ingest_batch({'sales':[sale]})['confirmations'][0]


def test_exact_retry_returns_same_ack_and_never_replaces_children(database):
    sale=payload(database);first=ingest(sale)
    assert first['status']=='synchronized'
    saved=deepcopy(database.tables)
    assert ingest(deepcopy(sale))==first
    assert database.tables==saved
    assert len(database.tables['Marnisi Fiscal Receipt'])==1


def test_conflicting_retry_preserves_all_financial_rows(database):
    sale=payload(database);assert ingest(sale)['status']=='synchronized'
    saved=deepcopy(database.tables);sale['items'][0]['si_name']='tampered'
    result=ingest(sale)
    assert result['status']=='error'
    assert 'Conflicting retry' in result['message']
    assert database.tables==saved


def test_payment_failure_rolls_back_parent_lines_receipts_and_counter(database):
    database.failed_table='Marnisi POS Sale Payment'
    result=ingest(payload(database))
    assert result['status']=='error'
    assert 'secret' not in result['message']
    assert all(not rows for rows in database.tables.values())
    assert not database.sequence


def test_receipt_failure_rolls_back_number_and_batch_keeps_other_success(database):
    first=payload(database);bad=deepcopy(first);bad['sales_num']='sale-bad';bad['receipt_snapshot']['sale_number']='sale-bad'
    bad['receipt_snapshot']['total']=999
    result=service.ingest_batch({'sales':[first,bad]})['confirmations']
    assert [row['status'] for row in result]==['synchronized','error']
    assert len(database.tables['Marnisi POS Sale'])==1
    assert list(database.sequence.values())==[1]


def test_stale_context_preserved_as_immutable_review(database):
    sale=payload(database);sale['receipt_snapshot']['fiscal_context']['vat_number']='old-vat'
    result=ingest(sale)
    assert result['fiscal_status']=='Review'
    assert result['receipt_snapshot']==sale['receipt_snapshot']
    assert result['fiscal_receipts'][0]['mode']=='Test'


def test_suspension_blocks_new_sales_but_preserves_recovery_ack(database):
    sale=payload(database);first=ingest(sale)
    database.settings['issuance_suspended']=1
    assert ingest(sale)==first
    different=deepcopy(sale);different['sales_num']='new-sale';different['receipt_snapshot']['sale_number']='new-sale'
    assert ingest(different)['status']=='error'


def test_live_requires_every_cutover_confirmation(database):
    database.settings['mode']='Live'
    with pytest.raises(ValueError,match='Verified seller'):
        service.validate_settings(database.settings)
    database.settings.update({key:'approved' for key in ('legal_name','vat_number','address','exo_number','approval_reference')})
    with pytest.raises(ValueError,match='Live requires'):
        service.validate_settings(database.settings)
    database.settings.update({key:1 for key in service.LIVE_CHECKS})
    service.validate_settings(database.settings)


def test_manual_creation_does_not_consume_counter_or_electronic_receipt(database):
    sale=payload(database);sale['manual_receipt_reference']='BOOK-0001';sale['manual_receipt_reason']='Printer outage'
    sale['receipt_snapshot']['manual_receipt_reference']='BOOK-0001'
    result=ingest(sale)
    assert result['fiscal_status']=='Manual'
    assert result['fiscal_receipts']==[]
    assert not database.sequence
    assert len(database.tables['Marnisi Manual Receipt Link'])==1


def test_database_deadlock_replays_entire_batch_and_discards_stale_acks(database,monkeypatch):
    from frappe.exceptions import QueryDeadlockError
    attempts=[];rollbacks=[]
    def run(row,**kwargs):
        attempts.append(row['sales_num'])
        if len(attempts)==2:raise QueryDeadlockError('concurrent row changed')
        return {'sale_num':row['sales_num'],'status':'synchronized','attempt':len(attempts)}
    monkeypatch.setattr(service,'ingest_one',run)
    monkeypatch.setattr(database,'rollback',lambda **kwargs:rollbacks.append(kwargs))
    result=service.ingest_batch({'sales':[{'sales_num':'first'},{'sales_num':'second'}]})
    assert attempts==['first','second','first','second']
    assert [ack['attempt'] for ack in result['confirmations']]==[3,4]
    assert rollbacks==[{}]


def test_database_conflict_exhaustion_returns_no_false_success(database,monkeypatch):
    from frappe.exceptions import QueryDeadlockError
    def run(row,**kwargs):
        if row['sales_num']=='second':raise QueryDeadlockError('lock conflict')
        return {'sale_num':row['sales_num'],'status':'synchronized'}
    monkeypatch.setattr(service,'ingest_one',run)
    monkeypatch.setattr(database,'rollback',lambda **kwargs:None)
    result=service.ingest_batch({'sales':[{'sales_num':'first'},{'sales_num':'second'}]})
    assert [ack['status'] for ack in result['confirmations']]==['error','error']


def test_refund_cannot_cross_test_live_modes(database,monkeypatch):
    monkeypatch.setattr(service,'_existing',lambda sale_num:{'name':'original','fiscal_protocol':1,'fiscal_status':'Test'})
    with pytest.raises(ValueError,match='cannot cross'):
        service._validate_refund({'return_source_sale_num':'original'},'Live')


def test_manager_manual_reservation_preserves_original_cashier_payload_and_suppresses_issue(database,monkeypatch):
    original=payload(database);frozen=deepcopy(original)
    monkeypatch.setattr(service,'require_pos_access',lambda *a,**k:{'user':'manager@example.invalid'})
    first=service.reserve_manual_receipt(original,'paper-42','Printer outage')
    assert service.reserve_manual_receipt(original,'paper-42','Printer outage')==first
    assert not database.tables['Marnisi POS Sale']
    assert not database.sequence
    assert original==frozen
    monkeypatch.setattr(service,'require_pos_access',lambda *a,**k:{'user':'cashier@example.invalid'})
    ack=ingest(original)
    assert ack['status']=='synchronized'
    assert ack['fiscal_status']=='Manual'
    assert ack['receipt_snapshot']==frozen['receipt_snapshot']
    assert ack['manual_receipt_reference']=='PAPER-42'
    assert ack['fiscal_receipts']==[]
    assert not database.sequence
    assert ingest(original)==ack


def test_manual_reservation_rejects_conflicting_frozen_payload(database):
    original=payload(database)
    service.reserve_manual_receipt(original,'paper-43','Printer outage')
    changed=deepcopy(original);changed['receipt_snapshot']['fiscal_context']['legal_name']='different'
    result=ingest(changed)
    assert result['status']=='error'
    assert 'manual reservation' in result['message']
    assert not database.tables['Marnisi POS Sale']
    assert not database.sequence


def test_manual_reservation_cannot_cover_an_electronically_issued_sale(database):
    original=payload(database)
    assert ingest(original)['fiscal_status']=='Test'
    with pytest.raises(ValueError,match='Electronic evidence already issued'):
        service.reserve_manual_receipt(original,'paper-44','Printer outage')
    assert not database.tables['Marnisi Manual Receipt Link']


def test_reconciliation_exposes_manual_reservations_before_cashier_upload(database,monkeypatch):
    from xirja_marnisi.fiscal.core import encode
    original=payload(database)
    def sql(query,values=(),as_dict=False):
        if 'JSON_EXTRACT(m.reserved_payload' in query:
            return [{'manual_reference':'PAPER-88','reserved_payload':encode(original)}]
        return []
    monkeypatch.setattr(database,'sql',sql)
    rows=service.reconciliation_rows({'from_date':'2026-09-01','to_date':'2026-09-30'})
    assert rows[0]['sale_num']==original['sales_num']
    assert rows[0]['manual_receipt_reference']=='PAPER-88'
    assert rows[0]['fiscal_status']=='Manual reserved'
    assert rows[0]['receipt_count']==0
    assert rows[0]['reconciliation_status']=='Manual reservation awaiting original cashier upload'
