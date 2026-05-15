# XIRJA Marnisi App Context Index

## Core Paths
- App root: `apps/xirja_marnisi`
- Python package: `apps/xirja_marnisi/xirja_marnisi`
- API modules: `apps/xirja_marnisi/xirja_marnisi/api`
- DocTypes: `apps/xirja_marnisi/xirja_marnisi/xirja_marnisi/doctype`
- Tests: `apps/xirja_marnisi/tests`
- Server seed scripts: `apps/xirja_marnisi/scripts`

## First Search
- Demo seed APIs: `xirja_marnisi/api/seed.py`
  - Includes POS tour catalog item seed (`Tour Silver`, `Tour Gold`, `Tour Platinum`) as `Vineyard Item` records.
  - Includes `enforce_single_store_setup` to enforce a single active vineyard (`Marnisi M'Xlokk`) and one register id (`Marnisi M'Xlokk-MAIN`) for POS behavior.
- Auth/session/context APIs: `xirja_marnisi/api/auth.py`, `xirja_marnisi/api/security.py`
  - `auth.get_context` also returns `ui_assets` (`login_background_image`, `app_background_image`) sourced from `Vineyard` image fields when present.
  - Receipt print constants for POS are backend-driven via `xirja_marnisi/api/settings.py` (`get_receipt_settings`) and included in `auth.get_context` as `receipt_settings`.
- Item APIs + stock movement logic: `xirja_marnisi/api/item.py`
- Tour package APIs: `xirja_marnisi/api/package.py`
- Tour booking/check-in status flow: `xirja_marnisi/api/booking.py`
- Frontend-compat bridge APIs: `xirja_marnisi/api/bridge.py`
  - Single-store and single-register enforcement for POS store/register feed lives in `bridge.py` (`_SINGLE_STORE_MODE`, `_LOCKED_STORE_ID`, `_SINGLE_REGISTER_MODE`) and always resolves one locked store (`Marnisi M'Xlokk`) with one main register.
  - In single-store mode, product payload keeps vineyard-scoped `item_id` but normalizes `item_store` to the locked store so POS always sees items under that single store.
  - POS sync stores parent sale + child lines in raw SQL tables: `tabMarnisi POS Sale`, `tabMarnisi POS Sale Item`, `tabMarnisi POS Sale Payment`.
  - Existing sales rows can be backfilled into child tables through `bridge.backfill_sales_children`.

## Backend UI Fields
- Vineyard background image fields for POS:
  - `xirja_marnisi/xirja_marnisi/doctype/vineyard/vineyard.json`
  - Fields: `pos_login_background_image`, `pos_app_background_image`
- Receipt print settings (single DocType):
  - `xirja_marnisi/xirja_marnisi/doctype/marnisi_settings/marnisi_settings.json`
  - Backend API reads values from `tabSingles` for `Marnisi Settings`.
- Vineyard Item image fields:
  - `xirja_marnisi/xirja_marnisi/doctype/vineyard_item/vineyard_item.json`
  - `image_path` is `Attach Image` (upload picker).
  - `image_file` is `Link -> File` (dropdown/search picker), and backend resolves it to `image_path` when needed.

## Demo Data Commands
- Seed built-in/fallback demo data:
  - `bash apps/xirja_marnisi/scripts/seed_demo_data.sh --site <site>`
- Seed from cross-bench Marsovin source:
  - `bash apps/xirja_marnisi/scripts/import_marsovin_seed.sh <source_bench> <source_site> <target_bench> <target_site> [limit]`

## Test Trigger
- From bench root:
  - `./run_all_tests.sh`

## Migration Patch
- Child-table backfill patch for legacy POS sales:
  - `xirja_marnisi/patches/v0_0_1/backfill_marnisi_pos_sale_children.py`
- Single-store enforcement patch:
  - `xirja_marnisi/patches/v0_0_1/enforce_single_marnisi_store.py`
