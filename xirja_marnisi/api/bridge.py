from __future__ import annotations

import json
from typing import Any

import frappe

from xirja_marnisi.api.security import get_accessible_vineyards, parse_args

_PAYMENT_METHODS = [
    {"payment_type_id": "1", "payment_type_name": "Cash"},
    {"payment_type_id": "2", "payment_type_name": "Pay Cheque -  BOV"},
    {"payment_type_id": "3", "payment_type_name": "Payment Customer Account"},
    {"payment_type_id": "4", "payment_type_name": "Loyality Redem"},
    {"payment_type_id": "7", "payment_type_name": "Card BOV"},
    {"payment_type_id": "8", "payment_type_name": "Pay Other Cheques"},
    {"payment_type_id": "9", "payment_type_name": "Gift Cards"},
    {"payment_type_id": "10", "payment_type_name": "Staff Vauchers"},
    {"payment_type_id": "12", "payment_type_name": "Stripe"},
    {"payment_type_id": "13", "payment_type_name": "Bank Transfer"},
]

_PERSONAL_USERS = [
    {
        "retail_personnel_id": "11111",
        "retail_user_group": "Vineyard Admin",
        "retail_user_first_name": "North",
        "retail_user_last_name": "Admin",
        "retail_user_email": "marnisi.admin.north@example.com",
    },
    {
        "retail_personnel_id": "22222",
        "retail_user_group": "Vineyard Admin",
        "retail_user_first_name": "South",
        "retail_user_last_name": "Admin",
        "retail_user_email": "marnisi.admin.south@example.com",
    },
    {
        "retail_personnel_id": "33333",
        "retail_user_group": "Vineyard Staff",
        "retail_user_first_name": "Vineyard",
        "retail_user_last_name": "Staff",
        "retail_user_email": "marnisi.staff@example.com",
    },
    {
        "retail_personnel_id": "44444",
        "retail_user_group": "Viewer",
        "retail_user_first_name": "Vineyard",
        "retail_user_last_name": "Viewer",
        "retail_user_email": "marnisi.viewer@example.com",
    },
]

_SALE_TABLE = "tabMarnisi POS Sale"
_SALE_ITEM_TABLE = "tabMarnisi POS Sale Item"
_SALE_PAYMENT_TABLE = "tabMarnisi POS Sale Payment"
_LOYALTY_TABLE = "tabMarnisi Loyalty User"
_SINGLE_STORE_MODE = True
_LOCKED_STORE_ID = "Marnisi M'Xlokk"
_SINGLE_REGISTER_MODE = True


def _resolve_vineyards() -> list[dict[str, Any]]:
    user = getattr(frappe.session, "user", "Guest")

    if user and user != "Guest":
        rows = get_accessible_vineyards(user)
        if rows:
            return rows

    return frappe.db.sql(
        """
        SELECT
            name AS vineyard,
            'Viewer' AS access_role,
            0 AS is_default,
            1 AS is_active
        FROM `tabVineyard`
        WHERE IFNULL(is_active, 1) = 1
        ORDER BY vineyard_name ASC
        """,
        as_dict=True,
    )


def _normalize_name(value: str) -> str:
    text = (value or "").strip()
    return text if text else "Unnamed Vineyard"


def _restrict_vineyards_for_pos(vineyards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _SINGLE_STORE_MODE:
        return vineyards

    normalized = [row for row in vineyards if (row.get("vineyard") or "").strip()]
    if not normalized:
        return normalized

    for row in normalized:
        if (row.get("vineyard") or "").strip() == _LOCKED_STORE_ID:
            return [row]

    return [normalized[0]]


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
    frappe.db.sql(f"ALTER TABLE `{table_name}` ADD COLUMN {column_sql}")


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


def _ensure_sales_tables() -> None:
    frappe.db.sql(
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

    frappe.db.sql(
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

    frappe.db.sql(
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

    frappe.db.sql(
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


@frappe.whitelist(allow_guest=True)
def get_all_users() -> dict[str, Any]:
    return _PERSONAL_USERS


@frappe.whitelist(allow_guest=True)
def get_all_stores() -> dict[str, Any]:
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
                "store_loyalty_enabled": 1,
                "store_loyalty_allow_earn": 0,
                "store_loyalty_allow_redeem": 0,
                "store_loyalty_show_customer_ui": 0,
                "store_loyalty_show_points_ui": 0,
                "store_loyalty_show_receipt_details": 0,
                "store_py_mthds_ava": [row["payment_type_id"] for row in _PAYMENT_METHODS],
            }
        )

    return out


@frappe.whitelist(allow_guest=True)
def get_all_registers() -> dict[str, Any]:
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


@frappe.whitelist(allow_guest=True)
def get_pay_mthds() -> dict[str, Any]:
    return _PAYMENT_METHODS


@frappe.whitelist(allow_guest=True)
def get_all_products() -> dict[str, Any]:
    rows = frappe.db.sql(
        """
        SELECT
            name,
            vineyard,
            item_code,
            item_name,
            category,
            brand,
            image_path,
            unit,
            sell_price,
            stock_qty
        FROM `tabVineyard Item`
        WHERE IFNULL(is_enabled, 1) = 1
        ORDER BY vineyard ASC, item_name ASC
        """,
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

        scoped_item_id = f"{vineyard}::{item_code}"

        out.append(
            {
                "item_id": scoped_item_id,
                "item_img_path": row.get("image_path") or "assets/items/1.png",
                "item_store": vineyard,
                "item_brand": row.get("brand") or "",
                "item_description": row.get("category") or "",
                "item_barcode": item_code,
                "item_name": row.get("item_name") or item_code,
                "item_qty": float(row.get("stock_qty") or 0),
                "item_price": float(row.get("sell_price") or 0),
                "item_category": row.get("category") or "",
                "item_unit": row.get("unit") or "Bottle",
                "item_tax_group": "VAT",
                "item_tax_pct": 18.0,
                "item_suppItems": [],
            }
        )

    return out


@frappe.whitelist(allow_guest=True)
def get_all_products_paola() -> dict[str, Any]:
    return get_all_products()


@frappe.whitelist(allow_guest=True)
def post_all_sales(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    sales_rows = payload.get("sales")
    if not isinstance(sales_rows, list):
        sales_rows = []

    _ensure_sales_tables()

    confirmations: list[dict[str, Any]] = []
    now = frappe.utils.now()
    actor = getattr(frappe.session, "user", "Guest")

    for row in sales_rows:
        if not isinstance(row, dict):
            continue

        sale_num = str(row.get("sales_num") or "").strip()
        if not sale_num:
            sale_num = f"SALE-{frappe.generate_hash(length=12)}"

        name = f"MPS-{sale_num}"
        if len(name) > 140:
            name = name[:140]

        loy_card = str(row.get("loy_cust_card_num") or "").strip()
        total = _as_float(row.get("sales_total"))
        items = row.get("items") if isinstance(row.get("items"), list) else []
        payments = (
            row.get("sale_pay_methods")
            if isinstance(row.get("sale_pay_methods"), list)
            else []
        )

        frappe.db.sql(
            """
            INSERT INTO `tabMarnisi POS Sale` (
                name, creation, modified, modified_by, owner, docstatus,
                sale_num, sales_store, sales_register_id, sales_date, sales_time,
                sales_cashier, sales_total, loy_cust_card_num, sales_payload
            )
            VALUES (
                %s, %s, %s, %s, %s, 0,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON DUPLICATE KEY UPDATE
                modified = VALUES(modified),
                modified_by = VALUES(modified_by),
                sales_store = VALUES(sales_store),
                sales_register_id = VALUES(sales_register_id),
                sales_date = VALUES(sales_date),
                sales_time = VALUES(sales_time),
                sales_cashier = VALUES(sales_cashier),
                sales_total = VALUES(sales_total),
                loy_cust_card_num = VALUES(loy_cust_card_num),
                sales_payload = VALUES(sales_payload)
            """,
            (
                name,
                now,
                now,
                actor,
                actor,
                sale_num,
                row.get("sales_store"),
                row.get("sales_registerId"),
                row.get("sales_date"),
                row.get("sales_time"),
                row.get("sales_cashier"),
                total,
                loy_card,
                json.dumps(row),
            ),
        )

        # Keep child rows deterministic and idempotent for repeated sync of same sale.
        frappe.db.sql(f"DELETE FROM `{_SALE_ITEM_TABLE}` WHERE parent = %s", (name,))
        frappe.db.sql(f"DELETE FROM `{_SALE_PAYMENT_TABLE}` WHERE parent = %s", (name,))

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
                    name,
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
                    name,
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

        confirmations.append(
            {
                "sale_num": sale_num,
                "status": "synchronized",
                "loy_cust_card_num": loy_card,
            }
        )

    frappe.db.commit()
    return {"confirmations": confirmations}


@frappe.whitelist(allow_guest=True)
def get_sales_history(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    _ensure_sales_tables()

    sale_num = str(payload.get("sale_num") or "").strip()
    item_code = str(payload.get("item_code") or "").strip().lower()
    item_name = str(payload.get("item_name") or "").strip().lower()
    from_date = str(payload.get("from_date") or "").strip()
    to_date = str(payload.get("to_date") or "").strip()
    sales_store = str(payload.get("sales_store") or "").strip()

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
            sales_payload
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

        sales.append(parsed)

    return {
        "status": "success",
        "sales": sales,
    }


@frappe.whitelist(allow_guest=True)
def xirja_loy_users() -> dict[str, Any]:
    _ensure_sales_tables()
    rows = frappe.db.sql(
        """
        SELECT
            loy_cust_card_num,
            loy_cust_first_name,
            loy_cust_last_name,
            loy_cust_email,
            loy_cust_city,
            loy_cust_mobile,
            loy_cust_scheme,
            loy_cust_balance,
            loy_cust_points,
            loy_cust_frozen
        FROM `tabMarnisi Loyalty User`
        ORDER BY modified DESC
        """,
        as_dict=True,
    )
    return {"status": "success", "users": rows}


@frappe.whitelist(allow_guest=True)
def get_retail_loy_user(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    card_num = str(payload.get("loy_cust_card_num") or "").strip()
    if not card_num:
        return {"status": "error", "message": "Loyalty card number is required"}

    _ensure_sales_tables()
    rows = frappe.db.sql(
        """
        SELECT
            loy_cust_card_num,
            loy_cust_first_name,
            loy_cust_last_name,
            loy_cust_email,
            loy_cust_city,
            loy_cust_mobile,
            loy_cust_scheme,
            loy_cust_balance,
            loy_cust_points,
            loy_cust_frozen
        FROM `tabMarnisi Loyalty User`
        WHERE loy_cust_card_num = %s
        LIMIT 1
        """,
        (card_num,),
        as_dict=True,
    )

    if not rows:
        return {"status": "error", "message": "User not found"}

    user = rows[0]
    user["loy_cust_primary_address"] = ""
    return {"status": "success", "user": user}


@frappe.whitelist(allow_guest=True)
def create_retail_loy_user(args: str = "") -> dict[str, Any]:
    payload = parse_args(args)
    card_num = str(payload.get("loy_cust_card_num") or "").strip()
    if not card_num:
        return {"status": "error", "message": "Loyalty card number is required"}

    first_name = str(payload.get("loy_cust_first_name") or "").strip()
    last_name = str(payload.get("loy_cust_last_name") or "").strip()
    email = str(payload.get("loy_cust_email") or "").strip()
    city = str(payload.get("loy_cust_city") or "").strip()
    mobile = str(payload.get("loy_cust_mobile") or "").strip()
    scheme = str(payload.get("loy_cust_scheme") or "SILVER").strip() or "SILVER"

    _ensure_sales_tables()
    now = frappe.utils.now()
    actor = getattr(frappe.session, "user", "Guest")
    name = f"MLU-{card_num}"
    if len(name) > 140:
        name = name[:140]

    frappe.db.sql(
        """
        INSERT INTO `tabMarnisi Loyalty User` (
            name, creation, modified, modified_by, owner, docstatus,
            loy_cust_card_num, loy_cust_first_name, loy_cust_last_name,
            loy_cust_email, loy_cust_city, loy_cust_mobile, loy_cust_scheme,
            loy_cust_balance, loy_cust_points, loy_cust_frozen
        )
        VALUES (
            %s, %s, %s, %s, %s, 0,
            %s, %s, %s, %s, %s, %s, %s,
            0, 0, 0
        )
        ON DUPLICATE KEY UPDATE
            modified = VALUES(modified),
            modified_by = VALUES(modified_by),
            loy_cust_first_name = VALUES(loy_cust_first_name),
            loy_cust_last_name = VALUES(loy_cust_last_name),
            loy_cust_email = VALUES(loy_cust_email),
            loy_cust_city = VALUES(loy_cust_city),
            loy_cust_mobile = VALUES(loy_cust_mobile),
            loy_cust_scheme = VALUES(loy_cust_scheme)
        """,
        (
            name,
            now,
            now,
            actor,
            actor,
            card_num,
            first_name,
            last_name,
            email,
            city,
            mobile,
            scheme,
        ),
    )

    frappe.db.commit()
    return {
        "status": "success",
        "user": {
            "loy_cust_card_num": card_num,
            "loy_cust_first_name": first_name,
            "loy_cust_last_name": last_name,
            "loy_cust_email": email,
            "loy_cust_city": city,
            "loy_cust_mobile": mobile,
            "loy_cust_scheme": scheme,
            "loy_cust_balance": 0,
            "loy_cust_points": 0,
            "loy_cust_frozen": 0,
        },
    }
