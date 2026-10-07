"""Backend catalogue administration and repeatable upgrade defaults."""
from types import SimpleNamespace
import pytest
from xirja_marnisi.api import item, bridge
from xirja_marnisi.fiscal import migrations_v2
from xirja_marnisi.fiscal.tenders import PAYMENT_METHODS, TENDERS


def test_configurable_flags_write_bound_sql_with_manager_permission(monkeypatch):
    queries=[];permissions=[]
    def sql(query,values=(),as_dict=False):
        queries.append((query,values))
        if 'SELECT name, vineyard, item_code' in query:return [{'name':'wine','vineyard':"Marnisi M'Xlokk",'item_code':'WINE'}]
        return []
    fake=SimpleNamespace(db=SimpleNamespace(sql=sql,commit=lambda:None),utils=SimpleNamespace(now=lambda:'2026-10-02'),throw=lambda message:(_ for _ in ()).throw(ValueError(message)))
    monkeypatch.setattr(item,'frappe',fake)
    monkeypatch.setattr(item,'parse_args',lambda value:value)
    monkeypatch.setattr(item,'require_authenticated_user',lambda:'manager@example.invalid')
    monkeypatch.setattr(item,'require_vineyard_permission',lambda vineyard,mutate:permissions.append((vineyard,mutate)))
    monkeypatch.setattr(item,'_has_image_file_column',lambda:False)
    result=item.update({'item_id':'wine','is_returnable':False,'allow_variable_price':True,'vat_rate':5})
    assert result['status']=='success'
    assert permissions==[("Marnisi M'Xlokk",True)]
    query,values=queries[-1]
    assert 'is_returnable = %s' in query and 'allow_variable_price = %s' in query and 'vat_rate = %s' in query
    assert values[:3]==(0,1,5.0)
    assert values[-1]=='wine'


def test_tenders_share_one_registry_and_keep_original_ids():
    assert bridge._PAYMENT_METHODS is PAYMENT_METHODS
    assert TENDERS=={'1':'Cash','7':'Card BOV','12':'Stripe','13':'Bank Transfer'}


def test_repeat_migration_does_not_reset_returnability(monkeypatch):
    recorded=[];installed=False
    def sql(query,values=()):
        nonlocal installed
        recorded.append((query,values))
        if query.startswith('SELECT version'):return [(2,)] if installed else []
        if query.startswith('INSERT INTO `tabMarnisi Checkout Schema`'):installed=True
        return []
    db=SimpleNamespace(sql=sql,sql_ddl=lambda query:None,exists=lambda doctype,name:True)
    monkeypatch.setattr(migrations_v2,'frappe',SimpleNamespace(db=db))
    migrations_v2.ensure_schema();migrations_v2.ensure_schema()
    updates=[query for query,values in recorded if query.startswith('UPDATE `tabVineyard Item`')]
    assert len(updates)==1
    inserts=[(query,values) for query,values in recorded if 'INSERT IGNORE INTO `tabVineyard Item`' in query]
    assert len(inserts)==2
    assert all('WHERE NOT EXISTS' in query for query,values in inserts)
    assert inserts[0][1][2]=="marnisi m'xlokk::tailored-experience"


def test_migration_uses_frappe_collation_and_repairs_prior_default_once(monkeypatch):
    ddl=[];collations={}
    def sql(query,values=()):
        if query.startswith('SELECT TABLE_COLLATION'):
            return [(collations.get(values[0],'utf8mb4_uca1400_ai_ci'),)]
        if query.startswith('SELECT version'):return [(2,)]
        return []
    def sql_ddl(query):
        ddl.append(query)
        if query.startswith('ALTER TABLE'):
            collations[query.split('`')[1]]='utf8mb4_unicode_ci'
    monkeypatch.setattr(migrations_v2,'frappe',SimpleNamespace(db=SimpleNamespace(sql=sql,sql_ddl=sql_ddl,exists=lambda *args:False)))
    migrations_v2.ensure_schema();migrations_v2.ensure_schema()
    assert all('COLLATE=utf8mb4_unicode_ci' in query for query in ddl if query.startswith('CREATE TABLE'))
    assert len([query for query in ddl if query.startswith('ALTER TABLE')])==4
