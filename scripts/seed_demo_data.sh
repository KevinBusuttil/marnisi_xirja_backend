#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  seed_demo_data.sh --site <target_site> [--bench <bench_path>] [--source-items-file <json_path>]

Examples:
  # Use fallback + in-site source tables only
  bash apps/xirja_marnisi/scripts/seed_demo_data.sh --site marnisi.local1

  # Seed from a pre-exported JSON file
  bash apps/xirja_marnisi/scripts/seed_demo_data.sh --site marnisi.local1 --source-items-file /tmp/marsovin_items.json
EOF
}

SITE=""
BENCH_DIR=""
SOURCE_ITEMS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --site)
      SITE="${2:-}"
      shift 2
      ;;
    --bench)
      BENCH_DIR="${2:-}"
      shift 2
      ;;
    --source-items-file)
      SOURCE_ITEMS_FILE="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$SITE" ]]; then
  echo "Error: --site is required." >&2
  usage
  exit 1
fi

if [[ -z "$BENCH_DIR" ]]; then
  BENCH_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
fi

if [[ ! -x "$BENCH_DIR/env/bin/python" ]]; then
  echo "Error: bench python not found at $BENCH_DIR/env/bin/python" >&2
  exit 1
fi

if [[ ! -f "$BENCH_DIR/Procfile" ]]; then
  echo "Error: bench root not detected at $BENCH_DIR (Procfile missing)." >&2
  exit 1
fi

cd "$BENCH_DIR"

if [[ -n "$SOURCE_ITEMS_FILE" ]]; then
  if [[ ! -f "$SOURCE_ITEMS_FILE" ]]; then
    echo "Error: source items file not found: $SOURCE_ITEMS_FILE" >&2
    exit 1
  fi

  KWARGS_JSON="$("$BENCH_DIR/env/bin/python" - <<'PY' "$SOURCE_ITEMS_FILE"
import json
import sys
print(json.dumps({"args": json.dumps({"source_items_path": sys.argv[1]})}))
PY
)"

  echo "Seeding demo data on site '$SITE' using source file '$SOURCE_ITEMS_FILE'..."
  bench --site "$SITE" execute xirja_marnisi.api.seed.seed_demo_data --kwargs "$KWARGS_JSON"
else
  echo "Seeding demo data on site '$SITE' using built-in/fallback source..."
  bench --site "$SITE" execute xirja_marnisi.api.seed.seed_demo_data
fi

echo "Seed complete."
