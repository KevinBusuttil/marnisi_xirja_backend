# XIRJA Marnisi App Context Index

## Fiscal Controls — First Search
- `xirja_marnisi/fiscal/core.py`: exact decimal money, canonical payload hashing and cumulative refund rounding.
- `xirja_marnisi/fiscal/validation.py`: independent protocol/snapshot/items/VAT/payments/headers validation and negative/pre-bill rejection.
- `xirja_marnisi/fiscal/service.py`: bound SQL, immutable retry acknowledgements, per-sale rollback, counters, original-linked refunds, manual linkage, read-only reconciliation and recovery suspension/high-watermarks.
- `xirja_marnisi/fiscal/documents.py`: generic create/update/delete/rename protection for sales/children/fiscal/manual evidence; administrator/manager settings and Live gates.
- `xirja_marnisi/fiscal/migrations.py`, versioned fiscal patch and `hooks.py`: installation/migration-only schema handling; historical IDs/payloads/prices remain unconverted and uncertified.
- DocTypes `marnisi_fiscal_settings`, `marnisi_fiscal_receipt`, `marnisi_manual_receipt_link`; raw fiscal counter table is service-owned. `Vineyard Item.vat_rate` is server-owned and starts at 18.
- `xirja_marnisi/api/fiscal.py`: Marnisi refund context/submission, manual linkage/reservation, reconciliation and recovery endpoints. Manual reservation binds an unchanged offline payload hash before original-cashier upload; outstanding paper reservations appear in reconciliation. Bridge routes retain the `confirmations` envelope and reject legacy uploads after coordinated cutover.
- `xirja_marnisi/api/auth.py`, `security.py`, DocType `marnisi_personal_id`: administrator-owned unique Personal ID → enabled Frappe User mapping, own password via Frappe LoginManager, session/CSRF, locked vineyard/register and existing access roles. No shared-password or ID-only reauthentication fallback.
- `integration/test_fiscal_acceptance.py`, `scripts/run_fiscal_integration.py`: real Frappe16 HTTP/session + independent-connection MariaDB checks; guard requires explicit marked disposable loopback site. Existing Marnisi sites must never be used.
- Certification hardening checks in `integration/test_fiscal_acceptance.py` cover approved synthetic Live issuance, service-level annual rollover, late receipt-write rollback, all five evidence tables' CRUD/db_set protection, HTTP recovery/Manager permissions, historical snapshot retrieval after catalogue/seller changes, and credential confidentiality on unexpected login failures. `tests/test_fiscal_immutable_evidence.py` exercises actual Frappe db_set hook ordering without a database.
- `xirja_marnisi/fiscal/documents.py` denies db_set in `before_change`, before SQL can overwrite evidence; `xirja_marnisi/api/auth.py` converts unexpected parsing/database login failures to sanitized AuthenticationError so Frappe cannot persist credential-bearing contextual tracebacks. Fiscal Settings grants both Vineyard Admin and Vineyard Manager read/write, while retaining Live/recovery validation.
- `FISCAL_ROLLOUT.md` points to coordinated rollout, physical Android acceptance, test evidence, F1–F7 matrix and selective manifest in the frontend repository.

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
- Explicit catalog price update: `scripts/update_pos_prices.py` (dry-run by default, requires `--apply` and a new backup file for writes). Exact nine-item price request, VAT-exclusive conversion at the current 18% rate, price-only resource API writes with optimistic timestamps, and post-update POS verification. Never invoked by migrations/seeds. Tests: `tests/test_pos_price_update.py`; price table and tax explanation: `README.md`, "POS Prices and VAT".
- Price contract: `bridge.get_all_products` sends `sell_price` unchanged as `item_price` and server-owned `item_tax_pct=vat_rate` (initially 18). Flutter adds VAT and rounds the unit gross price to two decimals. Keep three-decimal net Sell Prices when matching a requested inclusive total; do not save the inclusive target directly in Sell Price.
- Tour package APIs: `xirja_marnisi/api/package.py`
- Tour booking/check-in status flow: `xirja_marnisi/api/booking.py`
- Frontend-compat bridge APIs: `xirja_marnisi/api/bridge.py`
  - Single-store and single-register enforcement for POS store/register feed lives in `bridge.py` (`_SINGLE_STORE_MODE`, `_LOCKED_STORE_ID`, `_SINGLE_REGISTER_MODE`) and always resolves one locked store (`Marnisi M'Xlokk`) with one main register.
  - In single-store mode, product payload is now hard-filtered to locked vineyard items only (`vineyard = Marnisi M'Xlokk`) and keeps vineyard-scoped `item_id` while normalizing `item_store` to the locked store, so POS and Item Management match for that vineyard.
  - POS sync stores parent sale + child lines in raw SQL tables: `tabMarnisi POS Sale`, `tabMarnisi POS Sale Item`, `tabMarnisi POS Sale Payment`.
  - Historical child backfill is migration-only; request-time DDL and financial overwrite-on-retry are removed.

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
- Latest full EXO review: frontend `docs/fiscal/EXO_READINESS.md`; 502 passing cases and all named outcomes/current source hashes in `docs/fiscal/evidence/exo-review/`. External certification gates remain open.
- Optional `MARNISI_TEST_RESULTS_DIR` on the combined trigger writes sanitized full inventories. `scripts/run_fiscal_integration.py` owns disposable-site guards/collectors; `tests/test_fiscal_test_runner.py` covers sanitization, incomplete results and aggregate failure handling.
- From the frontend root (`/Users/trek-matrix/Work/Kevins_work/XIRJA-frontend-marnisi`):
  - Frontend `MARNISI_TEST_SITE=marnisi-fiscal-test.localhost MARNISI_REQUIRE_INTEGRATION=1 ./run_all_tests.sh` is the combined trigger. The bench-only runner is legacy; use the frontend trigger for development.

## Migration Patch
- Child-table backfill patch for legacy POS sales:
  - `xirja_marnisi/patches/v0_0_1/backfill_marnisi_pos_sale_children.py`
- Single-store enforcement patch:
  - `xirja_marnisi/patches/v0_0_1/enforce_single_marnisi_store.py`
