from xirja_marnisi.fiscal.service import reconciliation_rows


def execute(filters=None):
    columns = [
        {'fieldname': 'sale_num', 'label': 'Sale', 'fieldtype': 'Data', 'width': 220},
        {'fieldname': 'sales_date', 'label': 'Date', 'fieldtype': 'Date', 'width': 100},
        {'fieldname': 'fiscal_status', 'label': 'Evidence Status', 'fieldtype': 'Data', 'width': 110},
        {'fieldname': 'sales_total', 'label': 'Sale Total', 'fieldtype': 'Currency', 'width': 100},
        {'fieldname': 'payment_total', 'label': 'Payment Total', 'fieldtype': 'Currency', 'width': 100},
        {'fieldname': 'receipt_total', 'label': 'Receipt Total', 'fieldtype': 'Currency', 'width': 100},
        {'fieldname': 'payment_count', 'label': 'Payments', 'fieldtype': 'Int', 'width': 80},
        {'fieldname': 'receipt_count', 'label': 'Receipts', 'fieldtype': 'Int', 'width': 80},
        {'fieldname': 'return_source_sale_num', 'label': 'Original Sale', 'fieldtype': 'Data', 'width': 220},
        {'fieldname': 'manual_receipt_reference', 'label': 'Manual Reference', 'fieldtype': 'Data', 'width': 160},
        {'fieldname': 'reconciliation_status', 'label': 'Reconciliation', 'fieldtype': 'Data', 'width': 280},
    ]
    return columns, reconciliation_rows(filters or {})
