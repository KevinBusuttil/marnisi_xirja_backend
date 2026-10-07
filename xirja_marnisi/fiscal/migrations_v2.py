"""Additive v2 schema and one-time defaults. Only install/migrate invokes this."""
import frappe
from xirja_marnisi.fiscal.core import digest

VINEYARD = "Marnisi M'Xlokk"
TAILORED_CODE = 'TAILORED-EXPERIENCE'


def ensure_schema():
    frappe.db.sql_ddl('''CREATE TABLE IF NOT EXISTS `tabMarnisi Checkout Authorization` (
        sale_num VARCHAR(120) PRIMARY KEY, cart_hash CHAR(64) NOT NULL,
        authorization VARCHAR(64) NOT NULL UNIQUE, cashier VARCHAR(140) NOT NULL,
        fiscal_mode VARCHAR(10) NOT NULL, created_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')
    frappe.db.sql_ddl('''CREATE TABLE IF NOT EXISTS `tabMarnisi Cash Payout` (
        payout_uid VARCHAR(120) PRIMARY KEY, sale_num VARCHAR(120) NOT NULL UNIQUE,
        amount DECIMAL(18,2) NOT NULL, state VARCHAR(16) NOT NULL,
        handover_uid VARCHAR(120) NOT NULL DEFAULT '', cashier VARCHAR(140) NOT NULL,
        fiscal_mode VARCHAR(10) NOT NULL, created_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')
    frappe.db.sql_ddl('''CREATE TABLE IF NOT EXISTS `tabMarnisi Cash Payout Event` (
        name CHAR(64) PRIMARY KEY, payout_uid VARCHAR(120) NOT NULL,
        event_type VARCHAR(16) NOT NULL, handover_uid VARCHAR(120) NOT NULL,
        cashier VARCHAR(140) NOT NULL, created_at DATETIME(6) NOT NULL,
        UNIQUE KEY payout_event (payout_uid,event_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')
    frappe.db.sql_ddl('''CREATE TABLE IF NOT EXISTS `tabMarnisi Checkout Schema` (
        version INT PRIMARY KEY, installed_at DATETIME(6) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci''')
    # Match Frappe-managed evidence tables, even on MariaDB installations whose
    # server default is uca1400. Include pre-fiscal sale/child tables: their
    # existing text values remain unchanged, but history/payout joins must agree.
    for table in ('Marnisi POS Sale', 'Marnisi POS Sale Item', 'Marnisi POS Sale Payment',
                  'Marnisi Fiscal Counter', 'Marnisi Checkout Authorization',
                  'Marnisi Cash Payout', 'Marnisi Cash Payout Event', 'Marnisi Checkout Schema'):
        rows = frappe.db.sql('SELECT TABLE_COLLATION FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s', ('tab' + table,))
        if rows and rows[0][0] != 'utf8mb4_unicode_ci':
            frappe.db.sql_ddl('ALTER TABLE `tab' + table + '` CONVERT TO CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
    if not frappe.db.sql('SELECT version FROM `tabMarnisi Checkout Schema` WHERE version=2'):
        frappe.db.sql('UPDATE `tabVineyard Item` SET is_returnable=1 WHERE item_code<>%s', (TAILORED_CODE,))
        frappe.db.sql('INSERT INTO `tabMarnisi Checkout Schema` (version,installed_at) VALUES (2,NOW(6))')
    if not frappe.db.exists('Vineyard', VINEYARD):
        return
    # item_key is the established vineyard/code uniqueness key. INSERT IGNORE
    # preserves later name, price, tax, disabled and returnability configuration.
    item_key = VINEYARD.lower() + '::' + TAILORED_CODE.lower()
    frappe.db.sql('''INSERT IGNORE INTO `tabVineyard Item`
        (name,creation,modified,modified_by,owner,docstatus,idx,vineyard,item_key,
         item_code,item_name,category,unit,sell_price,stock_qty,is_enabled,vat_rate,
         is_returnable,allow_variable_price,image_path)
        SELECT %s,NOW(6),NOW(6),'Administrator','Administrator',0,0,%s,%s,%s,%s,
         'Experiences','Experience',0,0,1,18,0,1,'assets/items/1.png'
        WHERE NOT EXISTS (SELECT 1 FROM `tabVineyard Item` WHERE vineyard=%s AND item_code=%s)''',
        ('tailored-' + digest(item_key)[:30], VINEYARD, item_key, TAILORED_CODE,
         'Tailored Experience', VINEYARD, TAILORED_CODE))
