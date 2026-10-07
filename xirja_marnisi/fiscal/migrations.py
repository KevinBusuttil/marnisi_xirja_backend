"""Versioned, repeatable install/migrate DDL. Never called by request handlers."""
import frappe


def ensure_schema():
    # Legacy tables survive with original identifiers and payloads. Do not manufacture receipts.
    from xirja_marnisi.api.bridge import _ensure_sales_tables
    _ensure_sales_tables()
    frappe.db.sql_ddl('''CREATE TABLE IF NOT EXISTS `tabMarnisi Fiscal Counter` (
        name VARCHAR(140) PRIMARY KEY, vineyard VARCHAR(140) NOT NULL,
        mode VARCHAR(10) NOT NULL, sequence_year INT NOT NULL,
        last_number BIGINT NOT NULL DEFAULT 0,
        UNIQUE KEY vineyard_mode_year (vineyard,mode,sequence_year)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4''')
    # New nullable unique identity columns leave historical rows intact.
    for table, column in (('Marnisi POS Sale Item','line_uid'), ('Marnisi POS Sale Payment','payment_uid')):
        frappe.db.sql('UPDATE `tab' + table + '` SET `' + column + '`=NULL WHERE `' + column + "`=''")
    vineyard = "Marnisi M'Xlokk"
    if frappe.db.exists('Vineyard', vineyard):
        frappe.db.sql('''INSERT IGNORE INTO `tabMarnisi Fiscal Settings`
            (name,creation,modified,modified_by,owner,docstatus,idx,vineyard,mode,currency,
             approval_confirmed,catalogue_reviewed,all_terminals_upgraded,legacy_queue_cleared,
             backup_restore_verified,physical_acceptance_confirmed,outage_procedure_confirmed,issuance_suspended)
            VALUES (%s,NOW(6),NOW(6),'Administrator','Administrator',0,0,%s,'Disabled','EUR',0,0,0,0,0,0,0,0)''',
            ('MARNISI-FISCAL', vineyard))

    from xirja_marnisi.fiscal.migrations_v2 import ensure_schema as ensure_v2
    ensure_v2()
