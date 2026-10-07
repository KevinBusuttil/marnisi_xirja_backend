"""Repeated real migrations preserve legacy payloads, fiscal evidence and prices."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

import frappe
from test_fiscal_acceptance import BENCH, SITE, connect


class MigrationAcceptance(unittest.TestCase):
    def test_repeat_migration_preserves_history_and_prices(self):
        connect()
        try:
            marker='LEGACY-'+str(uuid.uuid4())
            payload=json.dumps({'sales_num':marker,'sales_date':'2020-01-01','items':[],
                'note':'Original legacy evidence: never manufacture a fiscal snapshot'})
            frappe.db.sql('''INSERT INTO `tabMarnisi POS Sale`
                (name,sale_num,sales_store,sales_register_id,sales_date,sales_time,sales_cashier,sales_total,sales_payload,fiscal_protocol)
                VALUES (%s,%s,%s,%s,'2020-01-01','10:00:00','historical-operator',12.34,%s,0)''',
                ('ORIGINAL-'+marker,marker,"Marnisi M'Xlokk","Marnisi M'Xlokk-MAIN",payload))
            frappe.db.commit()
            def fingerprint():
                tables=['Marnisi POS Sale','Marnisi POS Sale Item','Marnisi POS Sale Payment','Marnisi Fiscal Receipt','Marnisi Manual Receipt Link','Marnisi Fiscal Counter']
                state={table:frappe.db.sql('SELECT * FROM `tab'+table+'` ORDER BY name',as_dict=True) for table in tables}
                for table, key in (('Marnisi Checkout Authorization','sale_num'),('Marnisi Cash Payout','payout_uid'),('Marnisi Cash Payout Event','name')):
                    state[table]=frappe.db.sql('SELECT * FROM `tab'+table+'` ORDER BY `'+key+'`',as_dict=True)
                state['catalogue']=frappe.db.sql('SELECT name,item_name,sell_price,vat_rate,is_returnable,allow_variable_price FROM `tabVineyard Item` ORDER BY name',as_dict=True)
                return hashlib.sha256(json.dumps(state,sort_keys=True,default=str).encode()).hexdigest()
            before=fingerprint();frappe.db.rollback();frappe.destroy()
            for _ in range(2):
                with tempfile.TemporaryFile(mode='w+') as log:
                    result=subprocess.run(['bench','--site',SITE,'migrate'],cwd=BENCH,stdout=log,stderr=subprocess.STDOUT,timeout=120)
                    if result.returncode:
                        log.seek(0)
                        self.fail('Disposable migration failed: '+log.read()[-4000:])
            connect();after=fingerprint()
            self.assertEqual(after,before,'Repeated migration changed saved evidence or catalogue prices')
            row=frappe.db.sql('SELECT name,sale_num,sales_payload,fiscal_protocol,receipt_snapshot FROM `tabMarnisi POS Sale` WHERE sale_num=%s',(marker,),as_dict=True)[0]
            self.assertEqual(row['name'],'ORIGINAL-'+marker);self.assertEqual(row['sales_payload'],payload)
            self.assertFalse(row['fiscal_protocol']);self.assertFalse(row['receipt_snapshot'])
            self.assertFalse(frappe.db.exists('Marnisi Fiscal Receipt',{'sale_number':marker}))
        finally:
            frappe.destroy()

    def test_legacy_collation_repair_preserves_evidence_and_restores_joins(self):
        from xirja_marnisi.fiscal.migrations_v2 import ensure_schema
        from xirja_marnisi.api.bridge import get_sales_history
        from xirja_marnisi.fiscal.service import reconciliation_rows
        connect()
        try:
            def fingerprint():
                return hashlib.sha256(json.dumps(frappe.db.sql(
                    'SELECT * FROM `tabMarnisi POS Sale` ORDER BY name',as_dict=True),
                    sort_keys=True,default=str).encode()).hexdigest()
            before=fingerprint()
            frappe.db.sql_ddl('ALTER TABLE `tabMarnisi POS Sale` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci')
            ensure_schema();frappe.db.commit()
            self.assertEqual(fingerprint(),before)
            self.assertEqual(frappe.db.sql('SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s',('tabMarnisi POS Sale',))[0][0],'utf8mb4_unicode_ci')
            get_sales_history(json.dumps({'limit':1}))
            reconciliation_rows({'from_date':'2020-01-01','to_date':'2099-12-31'})
            ensure_schema();frappe.db.commit()
            self.assertEqual(fingerprint(),before)
        finally:
            frappe.destroy()
