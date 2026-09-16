from __future__ import annotations

import json
from typing import Any

import frappe

from xirja_marnisi.api.security import (
    get_accessible_vineyards,
    parse_args,
    require_authenticated_user,
    require_pos_access,
)

_PAYMENT_METHODS = [
    {"payment_type_id": "1", "payment_type_name": "Cash"},
    {"payment_type_id": "7", "payment_type_name": "Card BOV"},
]

_SALE_TABLE = "tabMarnisi POS Sale"
_SALE_ITEM_TABLE = "tabMarnisi POS Sale Item"
_SALE_PAYMENT_TABLE = "tabMarnisi POS Sale Payment"
_LOYALTY_TABLE = "tabMarnisi Loyalty User"
_SINGLE_STORE_MODE = True
_LOCKED_STORE_ID = "Marnisi M'Xlokk"
_SINGLE_REGISTER_MODE = True


def _resolve_vineyards() -> list[dict[str, Any]]:
    user = require_authenticated_user()
    return get_accessible_vineyards(user)


def _normalize_name(value: str) -> str:
    text = (value or "").strip()
    return text if text else "Unnamed Vineyard"


def _restrict_vineyards_for_pos(vineyards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _SINGLE_STORE_MODE:
        return vineyards

    return [row for row in vineyards if row.get("vineyard") == _LOCKED_STORE_ID][:1]


def _column_exists(table_name: str, column_name: str) -> bool:
    rows = frappe.db.sql(
        """
        SELECT 1
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        LIMIT 1
        """,
        (table_name, column_name),
    )
    return bool(rows)


def _add_column_if_missing(table_name: str, column_name: str, column_sql: str) -> None:
    if _column_exists(table_name, column_name):
        return
    frappe.db.sql_ddl(f"ALTER TABLE `{table_name}` ADD COLUMN {column_sql}")


def _as_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _build_child_row_name(prefix: str, sale_num: str, row_index: int) -> str:
    suffix = f"-{row_index:04d}"
    max_sale_len = max(1, 140 - len(prefix) - len(suffix))
    compact_sale_num = (sale_num or "")[:max_sale_len]
    return f"{prefix}{compact_sale_num}{suffix}"


def _effective_item_image_path_from_row(row: dict[str, Any]) -> str:
    image_path = str(row.get("image_path") or "").strip()
    if image_path:
        return image_path

    image_file_url = str(row.get("image_file_url") or "").strip()
    if image_file_url:
        return image_file_url

    return "assets/items/1.png"


def _replace_sale_children(
    *,
    parent_name: str,
    sale_num: str,
    items: list[dict[str, Any]],
    payments: list[dict[str, Any]],
    now: str,
    actor: str,
) -> None:
    # Keep child rows deterministic and idempotent for repeated sync of same sale.
    frappe.db.sql(f"DELETE FROM `{_SALE_ITEM_TABLE}` WHERE parent = %s", (parent_name,))
    frappe.db.sql(f"DELETE FROM `{_SALE_PAYMENT_TABLE}` WHERE parent = %s", (parent_name,))

    for item_idx, item_row in enumerate(items, start=1):
        if not isinstance(item_row, dict):
            continue

        item_row_name = _build_child_row_name("MPSI-", sale_num, item_idx)
        frappe.db.sql(
            f"""
            INSERT INTO `{_SALE_ITEM_TABLE}` (
                name, creation, modified, modified_by, owner, docstatus,
                parent, parentfield, parenttype, idx,
                si_sale_num, si_id, si_name, si_unit, si_barcode, si_category,
                si_qty, si_price, si_tax_pct, si_subtotal, si_tax, si_total,
                si_discount_amount, si_discount_percent, item_payload
            )
            VALUES (
                %s, %s, %s, %s, %s, 0,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s
            )
            """,
            (
                item_row_name,
                now,
                now,
                actor,
                actor,
                parent_name,
                "sales_items",
                "Marnisi POS Sale",
                item_idx,
                str(item_row.get("si_sale_num") or sale_num),
                str(item_row.get("si_id") or ""),
                str(item_row.get("si_name") or ""),
                str(item_row.get("si_unit") or ""),
                str(item_row.get("si_barcode") or ""),
                str(item_row.get("si_category") or ""),
                _as_float(item_row.get("si_qty")),
                _as_float(item_row.get("si_price")),
                _as_float(item_row.get("si_tax_pct")),
                _as_float(item_row.get("si_subtotal")),
                _as_float(item_row.get("si_tax")),
                _as_float(item_row.get("si_total")),
                _as_float(item_row.get("si_discount_amount")),
                _as_float(item_row.get("si_discount_percent")),
                json.dumps(item_row),
            ),
        )

    for pay_idx, pay_row in enumerate(payments, start=1):
        if not isinstance(pay_row, dict):
            continue

        payment_row_name = _build_child_row_name("MPSP-", sale_num, pay_idx)
        frappe.db.sql(
            f"""
            INSERT INTO `{_SALE_PAYMENT_TABLE}` (
                name, creation, modified, modified_by, owner, docstatus,
                parent, parentfield, parenttype, idx,
                pay_txn_sale_num, tender_type_id, payment_name, amount_tendered,
                payment_payload
            )
            VALUES (
                %s, %s, %s, %s, %s, 0,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
            (
                payment_row_name,
                now,
                now,
                actor,
                actor,
                parent_name,
                "sales_payments",
                "Marnisi POS Sale",
                pay_idx,
                sale_num,
                str(pay_row.get("tender_type_id") or ""),
                str(pay_row.get("payment_name") or ""),
                _as_float(pay_row.get("amount_tendered")),
                json.dumps(pay_row),
            ),
        )


def _backfill_sales_children_from_payload(limit: int = 0) -> dict[str, int]:
    # This helper is installation/migration-only. Never replace completed fiscal evidence.
    _ensure_sales_tables()

    legacy_filter = "AND COALESCE(s.fiscal_protocol, 0) = 0" if _column_exists(_SALE_TABLE, "fiscal_protocol") else ""
    query_limit = ""
    if limit > 0:
        query_limit = f"LIMIT {int(limit)}"

    rows = frappe.db.sql(
        f"""
        SELECT
            name,
            sale_num,
            sales_payload
        FROM `{_SALE_TABLE}` s
        WHERE NOT EXISTS (SELECT 1 FROM `tabMarnisi POS Sale Item` i WHERE i.parent=s.name)
        {legacy_filter}
        ORDER BY modified DESC
        {query_limit}
        """,
        as_dict=True,
    )

    actor = getattr(frappe.session, "user", "Guest")
    now = frappe.utils.now()
    processed = 0

    for row in rows:
        sale_num = str(row.get("sale_num") or "").strip()
        parent_name = str(row.get("name") or "").strip()
        if not sale_num or not parent_name:
            continue

        raw_payload = row.get("sales_payload")
        try:
            parsed = json.loads(raw_payload) if raw_payload else {}
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            continue

        items = parsed.get("items")
        if not isinstance(items, list):
            items = []

        payments = parsed.get("sale_pay_methods")
        if not isinstance(payments, list):
            payments = []

        _replace_sale_children(
            parent_name=parent_name,
            sale_num=sale_num,
            items=items,
            payments=payments,
            now=now,
            actor=actor,
        )
        processed += 1

    return {
        "scanned": len(rows),
        "processed": processed,
    }


def _ensure_sales_tables() -> None:
    frappe.db.sql_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS `{_SALE_TABLE}` (
            name VARCHAR(140) PRIMARY KEY,
            creation DATETIME(6),
            modified DATETIME(6),
            modified_by VARCHAR(140),
            owner VARCHAR(140),
            docstatus INT DEFAULT 0,
            idx INT NOT NULL DEFAULT 0,
            _user_tags LONGTEXT,
            _comments LONGTEXT,
            _assign LONGTEXT,
            _liked_by LONGTEXT,
            sale_num VARCHAR(140) UNIQUE,
            sales_store VARCHAR(140),
            sales_register_id VARCHAR(140),
            sales_date DATE,
            sales_time VARCHAR(32),
            sales_cashier VARCHAR(140),
            sales_total DECIMAL(18,6),
            loy_cust_card_num VARCHAR(140),
            sales_payload LONGTEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    # Keep legacy deployments compatible with Desk List views by backfilling
    # standard Frappe system columns that were missing in early raw SQL schema.
    _add_column_if_missing(_SALE_TABLE, "idx", "idx INT NOT NULL DEFAULT 0")
    _add_column_if_missing(_SALE_TABLE, "_user_tags", "_user_tags LONGTEXT")
    _add_column_if_missing(_SALE_TABLE, "_comments", "_comments LONGTEXT")
    _add_column_if_missing(_SALE_TABLE, "_assign", "_assign LONGTEXT")
    _add_column_if_missing(_SALE_TABLE, "_liked_by", "_liked_by LONGTEXT")

    frappe.db.sql_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS `{_SALE_ITEM_TABLE}` (
            name VARCHAR(140) PRIMARY KEY,
            creation DATETIME(6),
            modified DATETIME(6),
            modified_by VARCHAR(140),
            owner VARCHAR(140),
            docstatus INT DEFAULT 0,
            parent VARCHAR(140),
            parentfield VARCHAR(140),
            parenttype VARCHAR(140),
            idx INT NOT NULL DEFAULT 0,
            si_sale_num VARCHAR(140),
            si_id VARCHAR(140),
            si_name VARCHAR(255),
            si_unit VARCHAR(80),
            si_barcode VARCHAR(140),
            si_category VARCHAR(140),
            si_qty DECIMAL(18,6),
            si_price DECIMAL(18,6),
            si_tax_pct DECIMAL(18,6),
            si_subtotal DECIMAL(18,6),
            si_tax DECIMAL(18,6),
            si_total DECIMAL(18,6),
            si_discount_amount DECIMAL(18,6),
            si_discount_percent DECIMAL(18,6),
            item_payload LONGTEXT,
            INDEX idx_parent (parent),
            INDEX idx_si_sale_num (si_sale_num)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    frappe.db.sql_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS `{_SALE_PAYMENT_TABLE}` (
            name VARCHAR(140) PRIMARY KEY,
            creation DATETIME(6),
            modified DATETIME(6),
            modified_by VARCHAR(140),
            owner VARCHAR(140),
            docstatus INT DEFAULT 0,
            parent VARCHAR(140),
            parentfield VARCHAR(140),
            parenttype VARCHAR(140),
            idx INT NOT NULL DEFAULT 0,
            pay_txn_sale_num VARCHAR(140),
            tender_type_id VARCHAR(40),
            payment_name VARCHAR(140),
            amount_tendered DECIMAL(18,6),
            payment_payload LONGTEXT,
            INDEX idx_parent (parent),
            INDEX idx_pay_txn_sale_num (pay_txn_sale_num)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )

    frappe.db.sql_ddl(
        f"""
        CREATE TABLE IF NOT EXISTS `{_LOYALTY_TABLE}` (
            name VARCHAR(140) PRIMARY KEY,
            creation DATETIME(6),
            modified DATETIME(6),
            modified_by VARCHAR(140),
            owner VARCHAR(140),
            docstatus INT DEFAULT 0,
            loy_cust_card_num VARCHAR(140) UNIQUE,
            loy_cust_first_name VARCHAR(140),
            loy_cust_last_name VARCHAR(140),
            loy_cust_email VARCHAR(140),
            loy_cust_city VARCHAR(140),
            loy_cust_mobile VARCHAR(140),
            loy_cust_scheme VARCHAR(80),
            loy_cust_balance DECIMAL(18,6),
            loy_cust_points DECIMAL(18,6),
            loy_cust_frozen INT DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


@frappe.whitelist()
def get_all_users() -> list[dict[str, Any]]:
    actor = require_pos_access()['user']
    return frappe.db.sql("""SELECT personal_id AS retail_personnel_id, `user` AS retail_user_email
        FROM `tabMarnisi Personal ID` WHERE `user`=%s AND is_active=1""", (actor,), as_dict=True)


@frappe.whitelist()
def get_all_stores() -> dict[str, Any]:
    require_pos_access()
    vineyards = _restrict_vineyards_for_pos(_resolve_vineyards())
    out: list[dict[str, Any]] = []

    for row in vineyards:
        vineyard = (row.get("vineyard") or "").strip()
        if not vineyard:
            continue
        out.append(
            {
                "store_id": vineyard,
                "store_name": _normalize_name(vineyard),
                "store_address": "",
                "store_country": "Malta",
                "store_phone_num": "",
                "store_registration_num": "",
                "store_channel_type": "VINEYARD",
                "store_legal_entity": "marnisi",
                "store_vat_group": "STANDARD",
                "store_default_customer": "",
                "store_contact_address": "",
                "store_contact": "",
                "store_invent_location_id": vineyard,
                "store_bcrs_code": "",
                "store_opening_hours": "",
                "store_loyalty_enabled": 0,
                "store_loyalty_allow_earn": 0,
                "store_loyalty_allow_redeem": 0,
                "store_loyalty_show_customer_ui": 0,
                "store_loyalty_show_points_ui": 0,
                "store_loyalty_show_receipt_details": 0,
                "store_py_mthds_ava": [row["payment_type_id"] for row in _PAYMENT_METHODS],
                "fiscal_context": _public_fiscal_context(vineyard),
            }
        )

    return out


@frappe.whitelist()
def get_all_registers() -> dict[str, Any]:
    require_pos_access()
    vineyards = _restrict_vineyards_for_pos(_resolve_vineyards())
    out: list[dict[str, Any]] = []

    for row in vineyards:
        vineyard = (row.get("vineyard") or "").strip()
        if not vineyard:
            continue

        out.append(
            {
                "register_id": f"{vineyard}-MAIN",
                "register_name": "Main Register",
                "store_id": vineyard,
            }
        )
        if not _SINGLE_REGISTER_MODE:
            out.append(
                {
                    "register_id": f"{vineyard}-TASTING",
                    "register_name": "Tasting Register",
                    "store_id": vineyard,
                }
            )

    return out


@frappe.whitelist()
def get_pay_mthds() -> dict[str, Any]:
    require_pos_access()
    return _PAYMENT_METHODS


@frappe.whitelist()
def get_all_products() -> dict[str, Any]:
    require_pos_access()
    where_clause = "IFNULL(is_enabled, 1) = 1"
    query_values: tuple[Any, ...] = ()
    if _SINGLE_STORE_MODE:
        where_clause += " AND vineyard = %s"
        query_values = (_LOCKED_STORE_ID,)

    if _column_exists("tabVineyard Item", "image_file"):
        rows = frappe.db.sql(
            f"""
            SELECT
                name,
                vineyard,
                item_code,
                item_name,
                category,
                brand,
                image_path,
                (
                    SELECT file_url
                    FROM `tabFile`
                    WHERE name = image_file
                    LIMIT 1
                ) AS image_file_url,
                unit,
                sell_price,
                vat_rate,
                stock_qty
            FROM `tabVineyard Item`
            WHERE {where_clause}
            ORDER BY vineyard ASC, item_name ASC
            """,
            query_values,
            as_dict=True,
        )
    else:
        rows = frappe.db.sql(
            f"""
            SELECT
                name,
                vineyard,
                item_code,
                item_name,
                category,
                brand,
                image_path,
                '' AS image_file_url,
                unit,
                sell_price,
                vat_rate,
                stock_qty
            FROM `tabVineyard Item`
            WHERE {where_clause}
            ORDER BY vineyard ASC, item_name ASC
            """,
            query_values,
            as_dict=True,
        )

    out: list[dict[str, Any]] = []
    for row in rows:
        item_code = (row.get("item_code") or "").strip()
        vineyard = (row.get("vineyard") or "").strip()
        if not item_code:
            continue
        if not vineyard:
            continue

        effective_vineyard = _LOCKED_STORE_ID if _SINGLE_STORE_MODE else vineyard

        scoped_item_id = f"{vineyard}::{item_code}"

        out.append(
            {
                "item_id": scoped_item_id,
                "item_img_path": _effective_item_image_path_from_row(row),
                "item_store": effective_vineyard,
                "item_brand": row.get("brand") or "",
                "item_description": row.get("category") or "",
                "item_barcode": item_code,
                "item_name": row.get("item_name") or item_code,
                "item_qty": float(row.get("stock_qty") or 0),
                "item_price": float(row.get("sell_price") or 0),
                "item_category": row.get("category") or "",
                "item_unit": row.get("unit") or "Bottle",
                "item_tax_group": "VAT",
                "item_tax_pct": float(row.get("vat_rate") if row.get("vat_rate") is not None else 18),
                "item_suppItems": [],
            }
        )

    return out


@frappe.whitelist()
def get_all_products_paola() -> dict[str, Any]:
    return get_all_products()


@frappe.whitelist(methods=["POST"])
def post_all_sales(args: str = "") -> dict[str, Any]:
    from xirja_marnisi.fiscal.service import ingest_batch
    return ingest_batch(parse_args(args))


def backfill_sales_children(args: str = "") -> dict[str, Any]:
    raise ValueError("Child backfill is installation/migration-only; run bench migrate")


def _public_fiscal_context(vineyard):
    from xirja_marnisi.fiscal.service import get_public_context
    return get_public_context(vineyard)


@frappe.whitelist()
def get_sales_history(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    require_pos_access(payload.get("sales_store"))

    sale_num = str(payload.get("sale_num") or "").strip()
    item_code = str(payload.get("item_code") or "").strip().lower()
    item_name = str(payload.get("item_name") or "").strip().lower()
    from_date = str(payload.get("from_date") or "").strip()
    to_date = str(payload.get("to_date") or "").strip()
    sales_store = _LOCKED_STORE_ID

    limit = int(payload.get("limit") or 200)
    if limit < 1:
        limit = 200
    if limit > 2000:
        limit = 2000

    where = []
    params: list[Any] = []

    if sale_num:
        where.append("sale_num LIKE %s")
        params.append(f"%{sale_num}%")

    if from_date:
        where.append("sales_date >= %s")
        params.append(from_date)

    if to_date:
        where.append("sales_date <= %s")
        params.append(to_date)

    if sales_store:
        where.append("sales_store = %s")
        params.append(sales_store)

    where_clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = frappe.db.sql(
        f"""
        SELECT
            sale_num,
            sales_payload, acknowledgement, fiscal_status, receipt_snapshot,
            (SELECT manual_reference FROM `tabMarnisi Manual Receipt Link` m WHERE m.source_sale=`tabMarnisi POS Sale`.name) AS linked_manual_reference
        FROM `tabMarnisi POS Sale`
        {where_clause}
        ORDER BY sales_date DESC, sales_time DESC, modified DESC
        LIMIT {limit}
        """,
        tuple(params),
        as_dict=True,
    )

    sales: list[dict[str, Any]] = []
    for row in rows:
        raw_payload = row.get("sales_payload")
        try:
            parsed = json.loads(raw_payload) if raw_payload else {}
        except Exception:
            parsed = {}
        if not isinstance(parsed, dict):
            continue

        items = parsed.get("items")
        if not isinstance(items, list):
            items = []

        if item_code:
            if not any(item_code in str(item.get("si_id") or "").lower() for item in items if isinstance(item, dict)):
                continue

        if item_name:
            if not any(item_name in str(item.get("si_name") or "").lower() for item in items if isinstance(item, dict)):
                continue

        acknowledgement = json.loads(row.get("acknowledgement") or "{}")
        parsed["fiscal_status"] = row.get("fiscal_status") or "Legacy"
        parsed["fiscal_receipts"] = acknowledgement.get("fiscal_receipts", [])
        if row.get("receipt_snapshot"):
            parsed["receipt_snapshot"] = json.loads(row["receipt_snapshot"])
        if row.get("linked_manual_reference"):
            parsed["manual_receipt_reference"] = row["linked_manual_reference"]
            parsed["fiscal_status"] = "Manual"
        sales.append(parsed)

    return {
        "status": "success",
        "sales": sales,
    }


@frappe.whitelist()
def xirja_loy_users() -> dict[str, Any]:
    require_pos_access()
    return {"status": "success", "users": []}


@frappe.whitelist()
def get_retail_loy_user(args: str = "") -> dict[str, Any]:
    require_pos_access()
    return {"status": "error", "message": "Loyalty is not available in Marnisi"}
