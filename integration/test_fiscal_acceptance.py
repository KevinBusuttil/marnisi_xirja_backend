"""Real Frappe 16 HTTP/session and MariaDB transaction/concurrency acceptance.

Only the guarded runner may select the isolated disposable site. Synthetic fixtures
are committed for independent connection/HTTP visibility; never run against trading data.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import secrets
import threading
import unittest
from unittest.mock import patch
import uuid

import frappe
import requests
from werkzeug.serving import make_server, WSGIRequestHandler

SITE = os.environ['MARNISI_TEST_SITE']
BENCH = Path(os.environ['MARNISI_TEST_BENCH'])
VINEYARD = "Marnisi M'Xlokk"
REGISTER = VINEYARD + '-MAIN'


def connect(user='Administrator'):
    frappe.init(site=SITE, sites_path=str(BENCH / 'sites'))
    frappe.connect()
    frappe.set_user(user)


def uid():
    return str(uuid.uuid4())


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


class FiscalAcceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        connect()
        assert frappe.conf.marnisi_disposable_fiscal_test
        assert frappe.__version__ == '16.17.0'
        from frappe.utils.password import update_password
        if not frappe.db.exists('Vineyard', VINEYARD):
            frappe.get_doc({'doctype':'Vineyard','vineyard_code':VINEYARD,'vineyard_name':VINEYARD}).insert()
        for role in ('Vineyard Staff','Vineyard Admin','Vineyard Manager','Viewer'):
            if not frappe.db.exists('Role', role):
                frappe.get_doc({'doctype':'Role','role_name':role}).insert()
        cls.credentials = {}
        for kind, role in [('staff','Vineyard Staff'),('manager','Vineyard Admin'),('vineyard_manager','Vineyard Manager'),('viewer','Viewer')]:
            user=f'fiscal-{kind}@example.invalid'
            if not frappe.db.exists('User',user):
                frappe.get_doc({'doctype':'User','email':user,'first_name':kind,'enabled':1,
                    'send_welcome_email':0,'roles':[{'role':role}]}).insert()
            frappe.db.set_value('User',user,'enabled',1)
            password=secrets.token_urlsafe(32)
            update_password(user,password,logout_all_sessions=True)
            access=frappe.db.get_value('Vineyard User Access',{'user':user,'vineyard':VINEYARD})
            if not access:
                frappe.get_doc({'doctype':'Vineyard User Access','user':user,'vineyard':VINEYARD,
                    'access_role':role,'is_active':1,'is_default':1}).insert()
            mapping=frappe.db.get_value('Marnisi Personal ID',{'personal_id':f'FISCAL-{kind}'})
            if not mapping:
                frappe.get_doc({'doctype':'Marnisi Personal ID','personal_id':f'FISCAL-{kind}',
                    'user':user,'is_active':1}).insert()
            cls.credentials[kind]=(user,password)
        # Apply the checked-in permission definition to the guarded disposable
        # fixture, including an existing site provisioned before this permission.
        frappe.db.commit()
        frappe.reload_doc('xirja_marnisi', 'doctype', 'marnisi_fiscal_settings', force=True)
        cls.item=frappe.db.get_value('Vineyard Item',{'vineyard':VINEYARD,'item_code':'ACCEPTANCE-WINE'})
        if not cls.item:
            doc=frappe.get_doc({'doctype':'Vineyard Item','vineyard':VINEYARD,'item_code':'ACCEPTANCE-WINE',
                'item_name':'Acceptance wine','sell_price':10,'vat_rate':18,'is_enabled':1}).insert()
            cls.item=doc.name
        from xirja_marnisi.fiscal.service import settings_for
        settings=settings_for()
        if not settings:
            doc=frappe.get_doc({'doctype':'Marnisi Fiscal Settings','vineyard':VINEYARD,'mode':'Disabled','currency':'EUR'}).insert()
            cls.settings_name=doc.name
        else:
            cls.settings_name=settings['name']
        frappe.db.commit()
        import importlib
        app = importlib.import_module("frappe.app")
        app._site=SITE
        app._sites_path=str(BENCH/'sites')
        cls.server=make_server('127.0.0.1',0,app.application,threaded=True,request_handler=QuietHandler)
        cls.base=f'http://127.0.0.1:{cls.server.server_port}'
        cls.server_thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        frappe.destroy()

    def setUp(self):
        frappe.db.rollback()
        frappe.set_user('Administrator')
        frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET mode=%s,legal_name=%s,vat_number=%s,address=%s,exo_number=%s,currency=%s,issuance_suspended=0 WHERE name=%s',
            ('Test','Synthetic test seller','TEST-VAT','Synthetic address','TEST-EXO','EUR',self.settings_name))
        frappe.db.sql('UPDATE `tabVineyard Item` SET sell_price=10,vat_rate=18,item_name=%s,is_enabled=1 WHERE name=%s',('Acceptance wine',self.item))
        frappe.db.commit()

    def tearDown(self):
        frappe.db.rollback()
        frappe.set_user('Administrator')

    def payload(self, qty=1, actor='Administrator', split=False):
        from xirja_marnisi.fiscal.service import get_public_context
        sale=uid(); total=round(11.8*qty,2)
        line={'line_uid':uid(),'si_id':VINEYARD+'::ACCEPTANCE-WINE','si_name':'Acceptance wine',
            'si_qty':qty,'si_price':10,'si_tax_pct':18,'si_subtotal':10*qty,'si_tax':round(1.8*qty,2),
            'si_total':total,'si_discount_amount':0,'si_discount_percent':0,'si_unit':'Bottle'}
        payments=[{'payment_uid':uid(),'tender_type_id':'1','payment_name':'Cash','amount_tendered':total}]
        if split:
            payments[0]['amount_tendered']=5
            payments.append({'payment_uid':uid(),'tender_type_id':'7','payment_name':'Card BOV','amount_tendered':round(total-5,2)})
        snap={'sale_number':sale,'date':date.today().isoformat(),'time':'12:34:56','cashier':actor,
            'items':[deepcopy(line)],'payments':deepcopy(payments),'subtotal':10*qty,'tax':round(1.8*qty,2),
            'total':total,'discount':0,'change':0,'cash_tendered':payments[0]['amount_tendered'],
            'tax_rows':[{'rate':18,'net':10*qty,'tax':round(1.8*qty,2),'gross':total}],'fiscal_context':get_public_context()}
        return {'fiscal_protocol':1,'sales_num':sale,'sales_date':snap['date'],'sales_time':snap['time'],
            'sales_cashier':actor,'sales_store':VINEYARD,'sales_registerId':REGISTER,'items':[line],
            'sale_pay_methods':payments,'sales_subtotal':10*qty,'sales_tax':round(1.8*qty,2),'sales_total':total,
            'sales_discount_amount':0,'sales_change':0,'fiscal_final_total':total,'receipt_snapshot':snap}

    def ingest(self,payload,refund=False):
        from xirja_marnisi.fiscal.service import ingest_batch
        return ingest_batch({'sales':[payload]},refund=refund)['confirmations'][0]

    def login(self,kind='staff',password=None):
        session=requests.Session()
        response=session.post(self.base+'/api/method/xirja_marnisi.api.auth.login_with_personal_id',
            json={'args':{'personal_id':f'FISCAL-{kind}','password':password or self.credentials[kind][1]}},timeout=20)
        return session,response

    def refund(self, original, qty=1):
        result=self.payload(qty=-qty)
        result['return_source_sale_num']=original['sales_num'];result['return_reason']='Acceptance return'
        result['items'][0]['return_source_line_uid']=original['items'][0]['line_uid']
        result['sale_pay_methods'][0]['return_source_payment_uid']=original['sale_pay_methods'][0]['payment_uid']
        result['receipt_snapshot'].update({'return_source_sale_num':original['sales_num'],'return_reason':'Acceptance return',
            'items':deepcopy(result['items']),'payments':deepcopy(result['sale_pay_methods']),'cash_tendered':0})
        return result

    def test_split_retry_conflict_and_payment_uniqueness(self):
        payload=self.payload(split=True);ack=self.ingest(payload)
        self.assertEqual(ack['status'],'synchronized',ack)
        self.assertEqual(len(ack['fiscal_receipts']),2)
        self.assertEqual(self.ingest(payload),ack)
        conflict=deepcopy(payload);conflict['receipt_snapshot']['cashier']='Forged'
        self.assertEqual(self.ingest(conflict)['status'],'error')
        duplicate=self.payload();duplicate['sale_pay_methods'][0]['payment_uid']=payload['sale_pay_methods'][0]['payment_uid']
        duplicate['receipt_snapshot']['payments']=deepcopy(duplicate['sale_pay_methods'])
        self.assertEqual(self.ingest(duplicate)['status'],'error')
        self.assertFalse(frappe.db.exists('Marnisi POS Sale',{'sale_num':duplicate['sales_num']}))

    def test_batch_savepoint_rolls_back_partial_children(self):
        from xirja_marnisi.fiscal.service import ingest_batch
        first=self.payload();bad=self.payload();bad['items'][0]['line_uid']=first['items'][0]['line_uid']
        bad['receipt_snapshot']['items']=deepcopy(bad['items']);last=self.payload()
        acks=ingest_batch({'sales':[first,bad,last]})['confirmations']
        self.assertEqual([a['status'] for a in acks],['synchronized','error','synchronized'],acks)
        self.assertFalse(frappe.db.exists('Marnisi POS Sale',{'sale_num':bad['sales_num']}))
        self.assertFalse(frappe.db.exists('Marnisi POS Sale Payment',{'payment_uid':bad['sale_pay_methods'][0]['payment_uid']}))

    def test_snapshot_tampering_and_prebill_rejected(self):
        for field,value in [('date','2000-01-01'),('subtotal',1),('total',0),('cashier','forged'),('cash_tendered',100)]:
            with self.subTest(field=field):
                payload=self.payload();payload['receipt_snapshot'][field]=value
                self.assertEqual(self.ingest(payload)['status'],'error')
        payload=self.payload();payload['receipt_snapshot']['document_kind']='pre_bill'
        self.assertEqual(self.ingest(payload)['status'],'error')

    def test_stale_catalogue_and_settings_are_immutable_review(self):
        payload=self.payload();payload['items'][0]['si_name']='Earlier name';payload['receipt_snapshot']['items']=deepcopy(payload['items'])
        ack=self.ingest(payload);self.assertEqual(ack['fiscal_status'],'Review',ack)
        self.assertEqual(ack['receipt_snapshot']['items'][0]['si_name'],'Earlier name')
        changed=self.payload();changed['receipt_snapshot']['fiscal_context']['exo_number']='OLD'
        self.assertEqual(self.ingest(changed)['fiscal_status'],'Review')

    def test_concurrent_duplicate_and_number_allocation(self):
        payload=self.payload();frappe.db.commit();barrier=threading.Barrier(2)
        def submit(data):
            connect()
            try:
                barrier.wait(timeout=15);result=self.ingest(data);frappe.db.commit();return result
            finally:frappe.destroy()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(submit,[deepcopy(payload),deepcopy(payload)]))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0]['status'],'synchronized',results)
        frappe.db.rollback()
        self.assertEqual(frappe.db.count('Marnisi POS Sale',{'sale_num':payload['sales_num']}),1)
        barrier=threading.Barrier(2)
        payloads=[self.payload(),self.payload()];frappe.db.commit()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(submit,payloads))
        numbers=[result['fiscal_receipts'][0]['sequence_number'] for result in results]
        self.assertEqual(len(set(numbers)),2)
        self.assertEqual(abs(numbers[0]-numbers[1]),1)

    def test_concurrent_last_unit_refund_and_lost_ack(self):
        original=self.payload();self.assertEqual(self.ingest(original)['status'],'synchronized');frappe.db.commit()
        refunds=[self.refund(original),self.refund(original)];frappe.db.commit();barrier=threading.Barrier(2)
        def submit(payload):
            connect()
            try:
                barrier.wait(timeout=15);result=self.ingest(payload,refund=True);frappe.db.commit();return result
            finally:frappe.destroy()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(submit,refunds))
        self.assertEqual(sorted(r['status'] for r in results),['error','synchronized'],results)
        winner=next(i for i,r in enumerate(results) if r['status']=='synchronized')
        frappe.db.rollback();self.assertEqual(self.ingest(refunds[winner],refund=True),results[winner])
        self.assertEqual(self.ingest(self.refund(original),refund=True)['status'],'error')

    def test_recovery_suspension_and_live_guards(self):
        doc=frappe.get_doc('Marnisi Fiscal Settings',self.settings_name);doc.mode='Live'
        with self.assertRaises(Exception):doc.save()
        frappe.db.rollback()
        frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET issuance_suspended=1 WHERE name=%s',(self.settings_name,))
        self.assertEqual(self.ingest(self.payload())['status'],'error')

    def test_manual_reference_dedup_and_no_electronic_issue(self):
        payload=self.payload();ref='TESTBOOK/'+uid()
        payload.update({'manual_receipt_reference':ref,'manual_receipt_reason':'Offline exercise'})
        payload['receipt_snapshot']['manual_receipt_reference']=ref
        ack=self.ingest(payload);self.assertEqual(ack['fiscal_status'],'Manual',ack)
        self.assertEqual(ack['fiscal_receipts'],[])
        other=self.payload();other.update({'manual_receipt_reference':ref,'manual_receipt_reason':'Offline exercise'})
        other['receipt_snapshot']['manual_receipt_reference']=ref
        self.assertEqual(self.ingest(other)['status'],'error')

    def test_paper_reservation_preserves_original_operator_and_never_issues_electronic(self):
        from xirja_marnisi.fiscal.service import reserve_manual_receipt, reconciliation_rows
        original=self.payload(actor=self.credentials['staff'][0])
        original_evidence=deepcopy(original)
        reference='PAPER/'+uid()
        counters=frappe.db.sql('SELECT name,last_number FROM `tabMarnisi Fiscal Counter` ORDER BY name')
        frappe.set_user(self.credentials['staff'][0])
        with self.assertRaises(frappe.PermissionError):reserve_manual_receipt(original,reference,'Outage')
        frappe.set_user(self.credentials['manager'][0])
        reservation=reserve_manual_receipt(original,reference,'Outage')
        self.assertEqual(reservation['status'],'reserved')
        self.assertEqual(reserve_manual_receipt(original,reference,'Outage'),reservation)
        self.assertFalse(frappe.db.exists('Marnisi POS Sale',{'sale_num':original['sales_num']}))
        rows=reconciliation_rows({'from_date':date.today().isoformat(),'to_date':date.today().isoformat()})
        reserved=next(row for row in rows if row['sale_num']==original['sales_num'])
        self.assertEqual(reserved['fiscal_status'],'Manual reserved')
        self.assertEqual(frappe.db.sql('SELECT name,last_number FROM `tabMarnisi Fiscal Counter` ORDER BY name'),counters)
        altered=deepcopy(original);altered['receipt_snapshot']['cashier']='tampered'
        with self.assertRaises(ValueError):reserve_manual_receipt(altered,reference,'Outage')
        self.assertEqual(self.ingest(original)['status'],'error') # Manager cannot relabel cashier.
        frappe.set_user(self.credentials['staff'][0])
        ack=self.ingest(original)
        self.assertEqual(ack['status'],'synchronized',ack)
        self.assertEqual(ack['fiscal_status'],'Manual')
        self.assertEqual(ack['fiscal_receipts'],[])
        self.assertEqual(ack['receipt_snapshot'],original_evidence['receipt_snapshot'])
        self.assertEqual(self.ingest(original),ack)
        self.assertEqual(frappe.db.sql('SELECT name,last_number FROM `tabMarnisi Fiscal Counter` ORDER BY name'),counters)
        frappe.set_user(self.credentials['manager'][0])
        with self.assertRaises(ValueError):reserve_manual_receipt(self.payload(),reference,'Outage')

    def test_paper_reservation_after_electronic_issuance_is_blocked(self):
        from xirja_marnisi.fiscal.service import reserve_manual_receipt
        payload=self.payload();ack=self.ingest(payload)
        self.assertEqual(ack['fiscal_status'],'Test')
        with self.assertRaisesRegex(ValueError,'Electronic evidence'):
            reserve_manual_receipt(payload,'TOO-LATE/'+uid(),'Unknown previous acknowledgement')
        self.assertFalse(frappe.db.exists('Marnisi Manual Receipt Link',{'source_sale':'MPS-'+payload['sales_num']}))
        self.assertEqual(self.ingest(payload),ack)

    def test_fiscal_documents_generic_mutation_denied(self):
        payload=self.payload();ack=self.ingest(payload)
        doc=frappe.get_doc('Marnisi Fiscal Receipt',ack['fiscal_receipts'][0]['name'])
        doc.payment_amount=1
        with self.assertRaises(Exception):doc.save()
        with self.assertRaises(Exception):frappe.delete_doc('Marnisi Fiscal Receipt',doc.name)
        sale=frappe.get_doc('Marnisi POS Sale','MPS-'+payload['sales_num']);sale.sales_total=1
        with self.assertRaises(Exception):sale.save()

    def test_annual_mode_isolation_and_recovery_high_watermarks(self):
        from xirja_marnisi.fiscal.service import _next_sequence, settings_for, suspend_for_recovery, resume_after_reconciliation
        settings_for(lock=True)
        year=2041
        test1=_next_sequence(VINEYARD,'Test',year)
        live1=_next_sequence(VINEYARD,'Live',year)
        next_year=_next_sequence(VINEYARD,'Test',year+1)
        self.assertEqual((test1,live1,next_year),(1,1,1))
        self.assertEqual(_next_sequence(VINEYARD,'Test',year),2)
        suspend_for_recovery('synthetic-incident')
        with self.assertRaises(ValueError):resume_after_reconciliation('approval',[])
        rows=frappe.db.sql('SELECT mode,sequence_year,last_number FROM `tabMarnisi Fiscal Counter` WHERE vineyard=%s',(VINEYARD,),as_dict=True)
        watermarks=[{'mode':row['mode'],'year':row['sequence_year'],'last_number':row['last_number']+10} for row in rows]
        result=resume_after_reconciliation('synthetic-approval',watermarks)
        self.assertEqual(result['status'],'resumed')
        self.assertEqual(_next_sequence(VINEYARD,'Test',year),13)

    def test_partial_refund_final_rounding_original_tender_caps(self):
        from xirja_marnisi.fiscal.core import refund_allocation
        original=self.payload(qty=3)
        self.assertEqual(self.ingest(original)['status'],'synchronized')
        for index in range(3):
            refund=self.refund(original)
            self.assertEqual(self.ingest(refund,refund=True)['status'],'synchronized')
        self.assertEqual(self.ingest(self.refund(original),refund=True)['status'],'error')
        original=self.payload(split=True)
        self.assertEqual(self.ingest(original)['status'],'synchronized')
        refund=self.refund(original)
        self.assertEqual(self.ingest(refund,refund=True)['status'],'error')
        # Real source with 3 x rounded EUR 3.50; net EUR 8.90 has a residual cent.
        frappe.db.sql('UPDATE `tabVineyard Item` SET sell_price=2.966 WHERE name=%s',(self.item,))
        source=self.payload(qty=3)
        line=source['items'][0];line.update(si_price=2.966,si_subtotal=8.9,si_tax=1.6,si_total=10.5)
        source.update(sales_subtotal=8.9,sales_tax=1.6,sales_total=10.5,fiscal_final_total=10.5)
        source['sale_pay_methods'][0]['amount_tendered']=10.5
        snap=source['receipt_snapshot'];snap.update(items=deepcopy(source['items']),payments=deepcopy(source['sale_pay_methods']),subtotal=8.9,tax=1.6,total=10.5,cash_tendered=10.5,tax_rows=[{'rate':18,'net':8.9,'tax':1.6,'gross':10.5}])
        self.assertEqual(self.ingest(source)['status'],'synchronized')
        allocated=Decimal('0')
        for index in range(3):
            net=refund_allocation(3,8.9,index,allocated,1);allocated+=net;vat=Decimal('3.50')-net
            refund=self.refund(source);line=refund['items'][0];line.update(si_price=2.966,si_subtotal=-float(net),si_tax=-float(vat),si_total=-3.5)
            refund.update(sales_subtotal=-float(net),sales_tax=-float(vat),sales_total=-3.5,fiscal_final_total=-3.5)
            refund['sale_pay_methods'][0]['amount_tendered']=-3.5
            refund['receipt_snapshot'].update(items=deepcopy(refund['items']),payments=deepcopy(refund['sale_pay_methods']),subtotal=-float(net),tax=-float(vat),total=-3.5,tax_rows=[{'rate':18,'net':-float(net),'tax':-float(vat),'gross':-3.5}])
            self.assertEqual(self.ingest(refund,refund=True)['status'],'synchronized')
        self.assertEqual(allocated,Decimal('8.90'))

    def test_http_guest_wrong_password_session_csrf_and_roles(self):
        guest=requests.get(self.base+'/api/method/xirja_marnisi.api.bridge.get_sales_history',timeout=20)
        self.assertIn(guest.status_code,(401,403))
        _,wrong=self.login(password='Definitely-wrong-password')
        self.assertIn(wrong.status_code,(401,403))
        session,response=self.login();self.assertEqual(response.status_code,200,response.text[:300])
        token=response.json()['message']['csrf_token'];self.assertTrue(token)
        payload=self.payload(actor=self.credentials['staff'][0]);frappe.db.commit()
        endpoint=self.base+'/api/method/xirja_marnisi.api.bridge.post_all_sales'
        no_csrf=session.post(endpoint,json={'args':{'sales':[payload]}},timeout=20)
        self.assertIn(no_csrf.status_code,(400,403))
        good=session.post(endpoint,json={'args':{'sales':[payload]}},headers={'X-Frappe-CSRF-Token':token},timeout=20)
        self.assertEqual(good.status_code,200,good.text[:300]);self.assertEqual(good.json()['message']['confirmations'][0]['status'],'synchronized',good.text[:300])
        viewer,res=self.login('viewer');self.assertEqual(res.status_code,200,res.text[:300])
        denied=viewer.post(endpoint,json={'args':{'sales':[]}},headers={'X-Frappe-CSRF-Token':res.json()['message']['csrf_token']},timeout=20)
        self.assertIn(denied.status_code,(401,403))
        invalid=self.payload(actor=self.credentials['staff'][0]);invalid['sales_registerId']='Other';frappe.db.commit()
        denied=session.post(endpoint,json={'args':{'sales':[invalid]}},headers={'X-Frappe-CSRF-Token':token},timeout=20)
        self.assertEqual(denied.json()['message']['confirmations'][0]['status'],'error')
        session.post(self.base+'/api/method/logout',headers={'X-Frappe-CSRF-Token':token},timeout=20)
        revoked=session.get(self.base+'/api/method/xirja_marnisi.api.auth.get_context',timeout=20)
        self.assertIn(revoked.status_code,(401,403))

    def test_http_disabled_user(self):
        user=self.credentials['staff'][0]
        frappe.db.set_value('User',user,'enabled',0);frappe.db.commit();frappe.clear_cache(user=user)
        try:
            _,response=self.login();self.assertIn(response.status_code,(401,403))
        finally:
            frappe.db.set_value('User',user,'enabled',1);frappe.db.commit();frappe.clear_cache(user=user)

    def test_http_enabled_session_checks_live_user_and_vineyard_access(self):
        session, response = self.login()
        self.assertEqual(response.status_code, 200, response.text[:300])
        user = self.credentials['staff'][0]
        endpoint = self.base + '/api/method/xirja_marnisi.api.auth.get_context'
        access = frappe.db.get_value('Vineyard User Access', {'user': user, 'vineyard': VINEYARD})
        try:
            frappe.db.set_value('User', user, 'enabled', 0)
            frappe.db.commit()
            disabled = session.get(endpoint, timeout=20)
            self.assertIn(disabled.status_code, (401, 403))
        finally:
            frappe.db.set_value('User', user, 'enabled', 1)
            frappe.db.commit()
            frappe.clear_cache(user=user)
        session, response = self.login()
        self.assertEqual(response.status_code, 200, response.text[:300])
        try:
            frappe.db.set_value('Vineyard User Access', access, 'is_active', 0)
            frappe.db.commit()
            revoked = session.get(endpoint, timeout=20)
            self.assertIn(revoked.status_code, (401, 403))
        finally:
            frappe.db.set_value('Vineyard User Access', access, 'is_active', 1)
            frappe.db.commit()

    def test_http_refund_requires_manager_and_cannot_use_generic_crud(self):
        original = self.payload()
        self.assertEqual(self.ingest(original)['status'], 'synchronized')
        frappe.db.commit()
        staff, staff_login = self.login()
        manager, manager_login = self.login('manager')
        self.assertEqual(staff_login.status_code, 200, staff_login.text[:300])
        self.assertEqual(manager_login.status_code, 200, manager_login.text[:300])
        staff_headers = {'X-Frappe-CSRF-Token': staff_login.json()['message']['csrf_token']}
        manager_headers = {'X-Frappe-CSRF-Token': manager_login.json()['message']['csrf_token']}
        context_url = self.base + '/api/method/xirja_marnisi.api.fiscal.get_refund_context'
        denied = staff.post(context_url, json={'args': {'sale_num': original['sales_num']}}, headers=staff_headers, timeout=20)
        self.assertIn(denied.status_code, (401, 403))
        allowed = manager.post(context_url, json={'args': {'sale_num': original['sales_num']}}, headers=manager_headers, timeout=20)
        self.assertEqual(allowed.status_code, 200, allowed.text[:300])
        refund = self.refund(original)
        refund['sales_cashier'] = self.credentials['manager'][0]
        refund['receipt_snapshot']['cashier'] = self.credentials['manager'][0]
        endpoint = self.base + '/api/method/xirja_marnisi.api.fiscal.submit_linked_refund'
        denied = staff.post(endpoint, json={'args': {'sales': [refund]}}, headers=staff_headers, timeout=20)
        self.assertIn(denied.status_code, (401, 403))
        approved = manager.post(endpoint, json={'args': {'sales': [refund]}}, headers=manager_headers, timeout=20)
        self.assertEqual(approved.status_code, 200, approved.text[:300])
        acknowledgement = approved.json()['message']['confirmations'][0]
        self.assertEqual(acknowledgement['status'], 'synchronized', acknowledgement)
        retried = manager.post(endpoint, json={'args': {'sales': [refund]}}, headers=manager_headers, timeout=20)
        self.assertEqual(retried.json()['message']['confirmations'][0], acknowledgement)
        forbidden = manager.post(self.base + '/api/resource/Marnisi%20Fiscal%20Receipt', json={
            'doctype': 'Marnisi Fiscal Receipt', 'name': 'FORGED-' + uid(), 'payment_amount': 1,
        }, headers=manager_headers, timeout=20)
        self.assertIn(forbidden.status_code, (401, 403, 417))
        forged_id = 'FORGED-' + uid()
        forbidden_sale = manager.post(self.base + '/api/resource/Marnisi%20POS%20Sale', json={
            'doctype': 'Marnisi POS Sale', 'sale_num': forged_id, 'sales_total': 1,
        }, headers=manager_headers, timeout=20)
        self.assertIn(forbidden_sale.status_code, (401, 403, 417))
        frappe.db.rollback()
        self.assertFalse(frappe.db.exists('Marnisi POS Sale', {'sale_num': forged_id}))

    def test_http_manual_link_requires_manager(self):
        original = self.payload()
        original['items'][0]['si_name'] = 'Earlier catalogue description'
        original['receipt_snapshot']['items'] = deepcopy(original['items'])
        self.assertEqual(self.ingest(original)['fiscal_status'], 'Review')
        frappe.db.commit()
        args = {'sale_num': original['sales_num'], 'manual_receipt_reference': 'AUTH/' + uid(), 'reason': 'Outage exercise'}
        endpoint = self.base + '/api/method/xirja_marnisi.api.fiscal.link_manual_receipt'
        staff, login = self.login()
        self.assertEqual(login.status_code, 200, login.text[:300])
        denied = staff.post(endpoint, json={'args': args}, headers={'X-Frappe-CSRF-Token': login.json()['message']['csrf_token']}, timeout=20)
        self.assertIn(denied.status_code, (401, 403))
        manager, login = self.login('manager')
        self.assertEqual(login.status_code, 200, login.text[:300])
        approved = manager.post(endpoint, json={'args': args}, headers={'X-Frappe-CSRF-Token': login.json()['message']['csrf_token']}, timeout=20)
        self.assertEqual(approved.status_code, 200, approved.text[:300])
        self.assertEqual(approved.json()['message']['status'], 'success')

    def test_history_snapshot_and_reconciliation_read_only(self):
        from xirja_marnisi.fiscal.service import reconciliation_rows
        payload=self.payload(split=True);ack=self.ingest(payload)
        before=frappe.db.count('Marnisi Fiscal Receipt')
        rows=reconciliation_rows({'from_date':date.today().isoformat(),'to_date':date.today().isoformat()})
        row=next(r for r in rows if r['sale_num']==payload['sales_num'])
        self.assertEqual(Decimal(str(row['payment_total'])),Decimal('11.8'));self.assertEqual(Decimal(str(row['receipt_total'])),Decimal('11.8'))
        self.assertEqual(row['reconciliation_status'],'Matched');self.assertEqual(frappe.db.count('Marnisi Fiscal Receipt'),before)

    def test_approved_live_split_issuance_retry_and_mode_isolation(self):
        """Live is exercised only in this guarded disposable database, then rolled back."""
        from xirja_marnisi.fiscal.service import LIVE_CHECKS
        first_test = self.ingest(self.payload())
        self.assertEqual(first_test['fiscal_status'], 'Test')
        test_number = first_test['fiscal_receipts'][0]['sequence_number']
        year = first_test['fiscal_receipts'][0]['sequence_year']
        earlier_live = frappe.db.sql(
            'SELECT last_number FROM `tabMarnisi Fiscal Counter` WHERE vineyard=%s AND mode=%s AND sequence_year=%s',
            (VINEYARD, 'Live', year),
        )
        starting_live = int(earlier_live[0][0]) if earlier_live else 0
        approvals = ','.join('`' + field + '`=1' for field in LIVE_CHECKS)
        frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET mode=%s,approval_reference=%s,' + approvals + ' WHERE name=%s',
                      ('Live', 'SYNTHETIC-ACCEPTANCE-NOT-APPROVAL', self.settings_name))
        payload = self.payload(split=True)
        ack = self.ingest(payload)
        self.assertEqual(ack['status'], 'synchronized', ack)
        self.assertEqual(ack['fiscal_status'], 'Recorded')
        self.assertEqual([row['mode'] for row in ack['fiscal_receipts']], ['Live', 'Live'])
        self.assertEqual([row['sequence_number'] for row in ack['fiscal_receipts']], [starting_live + 1, starting_live + 2])
        self.assertEqual({row['payment_uid'] for row in ack['fiscal_receipts']}, {row['payment_uid'] for row in payload['sale_pay_methods']})
        self.assertEqual(sum(Decimal(str(row['payment_amount'])) for row in ack['fiscal_receipts']), Decimal('11.80'))
        before = frappe.db.count('Marnisi Fiscal Receipt')
        self.assertEqual(self.ingest(deepcopy(payload)), ack)
        self.assertEqual(frappe.db.count('Marnisi Fiscal Receipt'), before)
        frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET mode=%s WHERE name=%s', ('Test', self.settings_name))
        self.assertEqual(self.ingest(self.refund(payload), refund=True)['status'], 'error')
        next_test = self.ingest(self.payload())
        self.assertEqual(next_test['fiscal_receipts'][0]['sequence_number'], test_number + 1)
        self.assertEqual(self.ingest(payload), ack)  # Exact retry preserves original Live evidence after a mode change.

    def test_late_receipt_failure_rolls_back_every_row_and_allocated_number(self):
        from xirja_marnisi.fiscal import service
        first, bad, last = self.payload(), self.payload(split=True), self.payload()
        insert = service._insert
        failed_payment = bad['sale_pay_methods'][1]['payment_uid']

        def fail_second_receipt(doctype, data):
            if doctype == 'Marnisi Fiscal Receipt' and data['payment_uid'] == failed_payment:
                raise RuntimeError('Injected failure after receipt number allocation')
            return insert(doctype, data)

        with patch.object(service, '_insert', side_effect=fail_second_receipt):
            acks = service.ingest_batch({'sales': [first, bad, last]})['confirmations']
        self.assertEqual([ack['status'] for ack in acks], ['synchronized', 'error', 'synchronized'], acks)
        self.assertFalse(frappe.db.exists('Marnisi POS Sale', {'sale_num': bad['sales_num']}))
        for doctype, field, value in (
            ('Marnisi POS Sale Item', 'parent', 'MPS-' + bad['sales_num']),
            ('Marnisi POS Sale Payment', 'parent', 'MPS-' + bad['sales_num']),
            ('Marnisi Fiscal Receipt', 'source_sale', 'MPS-' + bad['sales_num']),
        ):
            self.assertEqual(frappe.db.count(doctype, {field: value}), 0)
        first_number = acks[0]['fiscal_receipts'][0]['sequence_number']
        self.assertEqual(acks[2]['fiscal_receipts'][0]['sequence_number'], first_number + 1)
        retry = self.ingest(bad)
        self.assertEqual(retry['status'], 'synchronized', retry)
        self.assertEqual([row['sequence_number'] for row in retry['fiscal_receipts']], [first_number + 2, first_number + 3])

    def test_http_generic_crud_and_rename_cannot_change_any_evidence_table(self):
        electronic = self.payload()
        ack = self.ingest(electronic)
        manual = self.payload()
        manual.update(manual_receipt_reference='CRUD/' + uid(), manual_receipt_reason='Synthetic outage')
        manual['receipt_snapshot']['manual_receipt_reference'] = manual['manual_receipt_reference']
        self.assertEqual(self.ingest(manual)['fiscal_status'], 'Manual')
        manual_name = frappe.db.get_value('Marnisi Manual Receipt Link', {'source_sale': 'MPS-' + manual['sales_num']})
        targets = [
            ('Marnisi POS Sale', 'MPS-' + electronic['sales_num'], 'sales_total', 1),
            ('Marnisi POS Sale Item', 'MPSI-' + electronic['items'][0]['line_uid'], 'si_total', 1),
            ('Marnisi POS Sale Payment', 'MPSP-' + electronic['sale_pay_methods'][0]['payment_uid'], 'amount_tendered', 1),
            ('Marnisi Fiscal Receipt', ack['fiscal_receipts'][0]['name'], 'payment_amount', 1),
            ('Marnisi Manual Receipt Link', manual_name, 'manual_reference', 'FORGED'),
        ]
        frappe.db.commit()
        manager, response = self.login('manager')
        self.assertEqual(response.status_code, 200, response.text[:300])
        headers = {'X-Frappe-CSRF-Token': response.json()['message']['csrf_token']}
        for doctype, name, field, value in targets:
            before = frappe.db.sql('SELECT * FROM `tab' + doctype + '` WHERE name=%s', (name,))
            resource = self.base + '/api/resource/' + requests.utils.quote(doctype, safe='') + '/' + requests.utils.quote(name, safe='')
            calls = [
                ('update', 'PUT', resource, {field: value}),
                ('delete', 'DELETE', resource, None),
                ('set_value', 'POST', self.base + '/api/method/frappe.client.set_value',
                 {'doctype': doctype, 'name': name, 'fieldname': field, 'value': value}),
                ('rename', 'POST', self.base + '/api/method/frappe.client.rename_doc',
                 {'doctype': doctype, 'old_name': name, 'new_name': 'FORGED-' + uid()}),
            ]
            for operation, method, endpoint, body in calls:
                with self.subTest(doctype=doctype, operation=operation):
                    denied = manager.request(method, endpoint, json=body, headers=headers, timeout=20)
                    self.assertIn(denied.status_code, (401, 403, 417), denied.text[:300])
                    frappe.db.rollback()
                    self.assertEqual(frappe.db.sql('SELECT * FROM `tab' + doctype + '` WHERE name=%s', (name,)), before)

    def test_db_set_denies_before_any_financial_row_changes(self):
        """A caller catching the denial must not be able to commit an altered row."""
        original = self.payload()
        ack = self.ingest(original)
        manual = self.payload()
        manual.update(manual_receipt_reference='DBSET/' + uid(), manual_receipt_reason='Synthetic outage')
        manual['receipt_snapshot']['manual_receipt_reference'] = manual['manual_receipt_reference']
        self.assertEqual(self.ingest(manual)['fiscal_status'], 'Manual')
        targets = [
            ('Marnisi POS Sale', 'MPS-' + original['sales_num'], 'sales_total', 1),
            ('Marnisi POS Sale Item', 'MPSI-' + original['items'][0]['line_uid'], 'si_total', 1),
            ('Marnisi POS Sale Payment', 'MPSP-' + original['sale_pay_methods'][0]['payment_uid'], 'amount_tendered', 1),
            ('Marnisi Fiscal Receipt', ack['fiscal_receipts'][0]['name'], 'payment_amount', 1),
            ('Marnisi Manual Receipt Link', frappe.db.get_value('Marnisi Manual Receipt Link', {'source_sale': 'MPS-' + manual['sales_num']}), 'manual_reference', 'FORGED'),
        ]
        for index, (doctype, name, field, value) in enumerate(targets):
            with self.subTest(doctype=doctype):
                savepoint = 'immutable_db_set_' + str(index)
                frappe.db.savepoint(savepoint)
                before = frappe.db.sql('SELECT * FROM `tab' + doctype + '` WHERE name=%s', (name,))
                try:
                    doc = frappe.get_doc(doctype, name)
                    with self.assertRaises(frappe.PermissionError):
                        doc.db_set(field, value)
                    self.assertEqual(frappe.db.sql('SELECT * FROM `tab' + doctype + '` WHERE name=%s', (name,)), before)
                finally:
                    frappe.db.rollback(save_point=savepoint)

    def test_http_recovery_requires_manager_csrf_and_complete_watermarks(self):
        self.assertEqual(self.ingest(self.payload())['status'], 'synchronized')
        frappe.db.commit()
        incident, approval = 'HTTP-INCIDENT/' + uid(), 'HTTP-APPROVAL/' + uid()
        endpoints = {action: self.base + '/api/method/xirja_marnisi.api.fiscal.' + action + '_issuance'
                     for action in ('suspend', 'resume')}
        sessions = {}
        for role in ('staff', 'viewer', 'manager'):
            session, login = self.login(role)
            self.assertEqual(login.status_code, 200, login.text[:300])
            sessions[role] = (session, {'X-Frappe-CSRF-Token': login.json()['message']['csrf_token']})
        for role in ('staff', 'viewer'):
            session, headers = sessions[role]
            for action in ('suspend', 'resume'):
                with self.subTest(role=role, action=action):
                    denied = session.post(endpoints[action], json={'args': {'reference': incident, 'high_watermarks': []}}, headers=headers, timeout=20)
                    self.assertIn(denied.status_code, (401, 403))
        manager, headers = sessions['manager']
        no_csrf = manager.post(endpoints['suspend'], json={'args': {'reference': incident}}, timeout=20)
        self.assertIn(no_csrf.status_code, (400, 403))
        suspended = manager.post(endpoints['suspend'], json={'args': {'reference': incident}}, headers=headers, timeout=20)
        self.assertEqual(suspended.status_code, 200, suspended.text[:300])
        self.assertEqual(suspended.json()['message']['status'], 'suspended')
        frappe.db.rollback()
        self.assertEqual(self.ingest(self.payload())['status'], 'error')
        frappe.db.rollback()  # Release the settings lock before the next HTTP request.
        incomplete = manager.post(endpoints['resume'], json={'args': {'reference': approval, 'high_watermarks': []}}, headers=headers, timeout=20)
        self.assertGreaterEqual(incomplete.status_code, 400)
        self.assertLess(incomplete.status_code, 600)
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value('Marnisi Fiscal Settings', self.settings_name, 'issuance_suspended'), 1)
        rows = frappe.db.sql('SELECT mode,sequence_year,last_number FROM `tabMarnisi Fiscal Counter` WHERE vineyard=%s', (VINEYARD,), as_dict=True)
        marks = [{'mode': row['mode'], 'year': row['sequence_year'], 'last_number': row['last_number']} for row in rows]
        frappe.db.rollback()
        args = {'reference': approval, 'high_watermarks': marks}
        no_csrf = manager.post(endpoints['resume'], json={'args': args}, timeout=20)
        self.assertIn(no_csrf.status_code, (400, 403))
        resumed = manager.post(endpoints['resume'], json={'args': args}, headers=headers, timeout=20)
        self.assertEqual(resumed.status_code, 200, resumed.text[:300])
        self.assertEqual(resumed.json()['message']['status'], 'resumed')
        frappe.db.rollback()
        self.assertEqual(self.ingest(self.payload())['status'], 'synchronized')

    def test_http_unexpected_login_failure_never_logs_credential_frames(self):
        """Capture error logging in memory; never write the synthetic marker to a log."""
        from frappe.utils import get_traceback
        database_class = type(frappe.local.db)
        original_sql = database_class.sql

        def unavailable_mapping(database, query, *args, **kwargs):
            if 'FROM `tabMarnisi Personal ID` m' in str(query):
                raise RuntimeError('Injected mapping lookup failure')
            return original_sql(database, query, *args, **kwargs)

        for representation in ('object', 'serialized', 'malformed'):
            with self.subTest(representation=representation):
                marker = secrets.token_urlsafe(24)
                captured = []

                def capture_context(*args, **kwargs):
                    captured.append(get_traceback(with_context=True))

                arguments = {'personal_id': 'FISCAL-staff', 'password': marker}
                if representation != 'object':
                    arguments = json.dumps(arguments)
                if representation == 'malformed':
                    arguments = arguments[:-1]
                with patch.object(database_class, 'sql', unavailable_mapping), patch('frappe.utils.error.log_error', side_effect=capture_context):
                    response = requests.post(self.base + '/api/method/xirja_marnisi.api.auth.login_with_personal_id',
                                             json={'args': arguments}, timeout=20)
                self.assertFalse(any(marker in trace for trace in captured), 'Credential entered contextual error logging')
                self.assertFalse(marker in response.text, 'Credential entered the HTTP response')
                self.assertIn(response.status_code, (401, 403))
                self.assertFalse(response.cookies.get('sid') not in (None, '', 'Guest'))

    def test_http_history_preserves_snapshot_after_catalogue_and_seller_change(self):
        original = self.payload(split=True)
        ack = self.ingest(original)
        self.assertEqual(ack['status'], 'synchronized', ack)
        before_receipts = frappe.db.sql('SELECT name,snapshot,payload_hash FROM `tabMarnisi Fiscal Receipt` WHERE source_sale=%s ORDER BY name',
                                       ('MPS-' + original['sales_num'],))
        before_counters = frappe.db.sql('SELECT name,last_number FROM `tabMarnisi Fiscal Counter` ORDER BY name')
        frappe.db.sql('UPDATE `tabVineyard Item` SET item_name=%s,sell_price=20,vat_rate=5,is_enabled=0 WHERE name=%s',
                      ('Changed after historical issuance', self.item))
        frappe.db.sql('UPDATE `tabMarnisi Fiscal Settings` SET legal_name=%s,vat_number=%s,exo_number=%s WHERE name=%s',
                      ('Changed seller', 'CHANGED-VAT', 'CHANGED-EXO', self.settings_name))
        frappe.db.commit()
        session, login = self.login('staff')
        self.assertEqual(login.status_code, 200, login.text[:300])
        response = session.get(self.base + '/api/method/xirja_marnisi.api.bridge.get_sales_history',
                               params={'args': json.dumps({'sale_num': original['sales_num']})}, timeout=20)
        self.assertEqual(response.status_code, 200, response.text[:300])
        sales = response.json()['message']['sales']
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]['receipt_snapshot'], original['receipt_snapshot'])
        self.assertEqual(sales[0]['items'], original['items'])
        self.assertEqual(sales[0]['sale_pay_methods'], original['sale_pay_methods'])
        self.assertEqual(sales[0]['fiscal_receipts'], ack['fiscal_receipts'])
        frappe.db.rollback()
        self.assertEqual(frappe.db.sql('SELECT name,snapshot,payload_hash FROM `tabMarnisi Fiscal Receipt` WHERE source_sale=%s ORDER BY name',
                                     ('MPS-' + original['sales_num'],)), before_receipts)
        self.assertEqual(frappe.db.sql('SELECT name,last_number FROM `tabMarnisi Fiscal Counter` ORDER BY name'), before_counters)

    def test_http_vineyard_manager_settings_permission_and_live_guards(self):
        manager_user = self.credentials['vineyard_manager'][0]
        roles = set(frappe.get_roles(manager_user))
        self.assertIn('Vineyard Manager', roles)
        self.assertFalse(roles.intersection({'System Manager', 'Vineyard Admin', 'Super Admin'}))
        endpoint = self.base + '/api/resource/Marnisi%20Fiscal%20Settings/' + requests.utils.quote(self.settings_name, safe='')
        for role in ('staff', 'viewer'):
            session, login = self.login(role)
            self.assertEqual(login.status_code, 200, login.text[:300])
            denied = session.put(endpoint, json={'legal_name': 'UNAUTHORIZED'},
                                 headers={'X-Frappe-CSRF-Token': login.json()['message']['csrf_token']}, timeout=20)
            self.assertIn(denied.status_code, (401, 403))
        session, login = self.login('vineyard_manager')
        self.assertEqual(login.status_code, 200, login.text[:300])
        headers = {'X-Frappe-CSRF-Token': login.json()['message']['csrf_token']}
        approved = session.put(endpoint, json={'legal_name': 'Manager reviewed synthetic seller'}, headers=headers, timeout=20)
        self.assertEqual(approved.status_code, 200, approved.text[:300])
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value('Marnisi Fiscal Settings', self.settings_name, 'legal_name'), 'Manager reviewed synthetic seller')
        frappe.db.rollback()
        denied_live = session.put(endpoint, json={'mode': 'Live'}, headers=headers, timeout=20)
        self.assertGreaterEqual(denied_live.status_code, 400)
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value('Marnisi Fiscal Settings', self.settings_name, 'mode'), 'Test')
        frappe.db.rollback()
        suspend = session.post(self.base + '/api/method/xirja_marnisi.api.fiscal.suspend_issuance',
                               json={'args': {'reference': 'MANAGER-INCIDENT/' + uid()}}, headers=headers, timeout=20)
        self.assertEqual(suspend.status_code, 200, suspend.text[:300])
        bypass = session.put(endpoint, json={'issuance_suspended': 0}, headers=headers, timeout=20)
        self.assertIn(bypass.status_code, (401, 403))
        frappe.db.rollback()
        self.assertEqual(frappe.db.get_value('Marnisi Fiscal Settings', self.settings_name, 'issuance_suspended'), 1)

    def test_service_year_rollover_uses_server_issuance_and_preserves_retries(self):
        from xirja_marnisi.fiscal import service
        before_midnight = self.payload()
        before_midnight['sales_date'] = '2081-12-30'
        before_midnight['receipt_snapshot']['date'] = '2081-12-30'
        with patch.object(service.frappe.utils, 'now', return_value='2081-12-31 23:59:59'):
            first = self.ingest(before_midnight)
        self.assertEqual(first['status'], 'synchronized', first)
        self.assertEqual(first['fiscal_receipts'][0]['sequence_year'], 2081)
        self.assertEqual(first['fiscal_receipts'][0]['sequence_number'], 1)
        delayed = self.payload()
        delayed['sales_date'] = '2081-12-31'
        delayed['receipt_snapshot']['date'] = '2081-12-31'
        with patch.object(service.frappe.utils, 'now', return_value='2082-01-01 00:00:01'):
            second = self.ingest(delayed)
            self.assertEqual(self.ingest(before_midnight), first)
            self.assertEqual(self.ingest(delayed), second)
        self.assertEqual(second['status'], 'synchronized', second)
        self.assertEqual(second['fiscal_receipts'][0]['sequence_year'], 2082)
        self.assertEqual(second['fiscal_receipts'][0]['sequence_number'], 1)
        self.assertEqual(second['receipt_snapshot']['date'], '2081-12-31')
        self.assertNotEqual(first['fiscal_receipts'][0]['name'], second['fiscal_receipts'][0]['name'])
        self.assertEqual(frappe.db.sql('SELECT sequence_year,last_number FROM `tabMarnisi Fiscal Counter` WHERE vineyard=%s AND mode=%s AND sequence_year IN (2081,2082) ORDER BY sequence_year',
                                     (VINEYARD, 'Test')), ((2081, 1), (2082, 1)))
