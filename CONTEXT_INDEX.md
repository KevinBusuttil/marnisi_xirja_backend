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
- Auth/session/context APIs: `xirja_marnisi/api/auth.py`, `xirja_marnisi/api/security.py`
- Item APIs + stock movement logic: `xirja_marnisi/api/item.py`
- Tour package APIs: `xirja_marnisi/api/package.py`
- Tour booking/check-in status flow: `xirja_marnisi/api/booking.py`
- Frontend-compat bridge APIs: `xirja_marnisi/api/bridge.py`

## Demo Data Commands
- Seed built-in/fallback demo data:
  - `bash apps/xirja_marnisi/scripts/seed_demo_data.sh --site <site>`
- Seed from cross-bench Marsovin source:
  - `bash apps/xirja_marnisi/scripts/import_marsovin_seed.sh <source_bench> <source_site> <target_bench> <target_site> [limit]`

## Test Trigger
- From bench root:
  - `./run_all_tests.sh`
