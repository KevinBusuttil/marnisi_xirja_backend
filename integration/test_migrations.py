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
                state['catalogue']=frappe.db.sql('SELECT name,sell_price,vat_rate FROM `tabVineyard Item` ORDER BY name',as_dict=True)
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
