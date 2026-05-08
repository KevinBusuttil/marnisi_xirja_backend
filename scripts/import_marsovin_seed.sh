#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  echo "Usage: $0 <source_bench_path> <source_site> <target_bench_path> <target_site> [limit]"
  echo "Example: $0 /opt/frappe-bench-xirja xirja.local /opt/frappebench-marnisi marnisi.local1 120"
  exit 1
fi

SOURCE_BENCH="$1"
SOURCE_SITE="$2"
TARGET_BENCH="$3"
TARGET_SITE="$4"
LIMIT="${5:-120}"

TMP_JSON="$(mktemp /tmp/marnisi_marsovin_items.XXXXXX.json)"
cleanup() {
  rm -f "$TMP_JSON"
}
trap cleanup EXIT

echo "[1/3] Exporting Marsovin + Maltese Wines from source DB: ${SOURCE_SITE}"
"${SOURCE_BENCH}/env/bin/python" - <<'PY' "$SOURCE_BENCH" "$SOURCE_SITE" "$TMP_JSON" "$LIMIT"
import json
import sys
from pathlib import Path
import pymysql

source_bench = Path(sys.argv[1]).resolve()
source_site = sys.argv[2]
target_json = Path(sys.argv[3]).resolve()
limit = int(sys.argv[4])

site_config_path = source_bench / "sites" / source_site / "site_config.json"
common_site_config_path = source_bench / "sites" / "common_site_config.json"

if not site_config_path.exists():
    raise SystemExit(f"site_config.json not found for site: {source_site}")

site_config = json.loads(site_config_path.read_text(encoding="utf-8"))
common_config = {}
if common_site_config_path.exists():
    common_config = json.loads(common_site_config_path.read_text(encoding="utf-8"))

db_name = site_config.get("db_name")
db_user = site_config.get("db_name")
db_password = site_config.get("db_password")
db_host = site_config.get("db_host") or common_config.get("db_host") or "127.0.0.1"
db_port = int(site_config.get("db_port") or common_config.get("db_port") or 3306)

if not db_name or not db_user or db_password is None:
    raise SystemExit("Invalid source DB config: db_name/db_password missing")

conn = pymysql.connect(
    host=db_host,
    port=db_port,
    user=db_user,
    password=db_password,
    database=db_name,
    charset="utf8mb4",
    cursorclass=pymysql.cursors.DictCursor,
)

with conn:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                item_id,
                item_name,
                item_category,
                item_brand,
                item_price,
                item_qty
            FROM `tabRetail Items`
            WHERE item_category = 'Maltese Wines'
              AND item_brand LIKE 'Marsovin%%'
              AND IFNULL(item_price, 0) > 0
            ORDER BY item_name ASC
            LIMIT %s
            """,
            (limit,),
        )
        rows = cursor.fetchall()

items = []
for row in rows:
    items.append(
        {
            "item_code": row.get("item_id"),
            "item_name": row.get("item_name"),
            "category": row.get("item_category") or "Maltese Wines",
            "brand": row.get("item_brand") or "Marsovin",
            "price": float(row.get("item_price") or 0),
            "stock": float(row.get("item_qty") or 0),
        }
    )

target_json.write_text(json.dumps(items), encoding="utf-8")
print(f"Exported items: {len(items)}")
PY

echo "[2/3] Seeding target site: ${TARGET_SITE}"
(
  cd "$TARGET_BENCH"
  KWARGS_JSON="$("$TARGET_BENCH/env/bin/python" - <<'PY' "$TMP_JSON"
import json
import sys
print(json.dumps({"args": json.dumps({"source_items_path": sys.argv[1]})}))
PY
)"
  bench --site "$TARGET_SITE" execute xirja_marnisi.api.seed.seed_demo_data --kwargs "$KWARGS_JSON"
)

echo "[3/3] Completed"
echo "Marnisi seed import completed."
