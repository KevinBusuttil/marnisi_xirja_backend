# Marnisi 1.1.0+10 — server test build and deployment

Prepared 8 October 2026. This package targets `http://146.190.236.171` on a fresh
installation. Existing installations retain their saved connection settings.
The HTTP exception is compiled for that origin only; other public HTTP hosts and
ports remain blocked. Normal builds retain the HTTPS default. HTTP transmits
credentials and transactions without transport encryption, so this is a test
delivery, not an approved production/EXO release.

The APK is a release-mode build using the repository's existing **debug signing
key**. It has not been signed with a supplied production key. An existing tablet
can only be upgraded with a compatible signing certificate; never uninstall or
clear its data to work around a signing mismatch. Use a spare device first.

## What this delivery does not change

This release does not add support for pre-fiscal app uploads or Personal-ID-only
login. The current old app cannot continue against this backend after cutover.
Protocol-1 fiscal queues remain supported; pre-fiscal queues are a different
format and must be drained against the old backend before migration. Preserved
historical records are not converted into newly issued fiscal receipts.

To keep old tablets trading while testing, run the new backend in a separate
bench/container and test database with a separate address/port. Two sites in the
same bench share application code. This APK's HTTP exception is restricted to
the requested origin, including port 80: another HTTP test port requires a new
explicit build. A valid HTTPS test hostname can be entered in Connection settings.

Test mode is not database isolation. Do not put synthetic sales into a trading
database and plan to delete them later. Test receipts and cash-refund obligations
are still persistent records. This handoff does not deploy or change the server.

## Push the two local commits

Use the commit IDs supplied with the delivery. Push only after reviewing them:

```sh
git -C /Users/trek-matrix/Work/Kevins_work/frappebench-marnisi/apps/xirja_marnisi push origin main
git -C /Users/trek-matrix/Work/Kevins_work/XIRJA-frontend-marnisi push origin apply-bixolon-printing
```

The frontend does not need installing into the server's Frappe bench; it is the
Android APK. The server app is `xirja_marnisi` from `marnisi_xirja_backend`.

## Server deployment sequence

The server's actual bench path and Frappe site name have not been inspected.
An IP address does not establish the site name. Replace the placeholders below,
confirm which site the IP's web-server virtual host serves, and use the normal
bench service account. The verified local runtime is Frappe 16.17.0 / Python 3.14;
the backend declares Python >=3.14. Resolve runtime differences in staging first.

1. Restore a backup into an isolated staging environment and rehearse the upgrade.
   For a trading-site cutover, stop new sales, synchronize every old tablet under
   its original operator, and reconcile all pending/failed/unknown queues first.
2. Back up database, public/private files and site configuration/encryption key.
   Back up each closed tablet's SQLite database and WAL/SHM consistently. Record
   current source revisions, receipt/counter high-watermarks and scheduler state.
3. Enter maintenance mode and stop the scheduler for the selected site. Confirm
   the app checkout is clean or preserve/review local source changes. Do not use
   `reset --hard`, broad `bench update`, or run the demo seeder on business data.

```sh
cd /actual/path/to/frappe-bench
export MARNISI_SITE='actual-site-name'
export MARNISI_BACKEND_COMMIT='paste-backend-commit-from-delivery'
bench version
bench --site "$MARNISI_SITE" list-apps
git -C apps/xirja_marnisi status --short
bench --site "$MARNISI_SITE" set-maintenance-mode on
bench --site "$MARNISI_SITE" disable-scheduler
bench --site "$MARNISI_SITE" backup --with-files
```

Check that the backup succeeded and retain an off-server copy plus the original
site configuration before proceeding. Run the following commands one at a time;
stop on any failure and retain maintenance mode while investigating.

```sh
git -C apps/xirja_marnisi fetch origin
git -C apps/xirja_marnisi merge --ff-only "$MARNISI_BACKEND_COMMIT"
bench setup requirements --python xirja_marnisi
bench --site "$MARNISI_SITE" migrate
bench --site "$MARNISI_SITE" clear-cache
bench build --app xirja_marnisi
bench restart
```

Use the configured process manager's equivalent restart for a container-managed
bench. Do not restart unrelated benches. Once startup/migration checks succeed:

```sh
bench --site "$MARNISI_SITE" set-maintenance-mode off
```

Restore the scheduler to its previous state; if it was previously enabled, use
`bench --site "$MARNISI_SITE" enable-scheduler`. Leave an isolated test site's
scheduler/integrations disabled unless a particular test needs them.

4. In Desk, configure **Marnisi Fiscal Settings** for `Marnisi M'Xlokk` in **Test**
   mode on the isolated test site, with deliberate test seller/VAT/address/EXO
   details and EUR. Do not tick unfulfilled Live approval gates.
5. For each tester, create/enable their Frappe User with their own password, an
   active **Marnisi Personal ID** mapping, and active **Vineyard User Access** for
   `Marnisi M'Xlokk`. A cashier uses `Vineyard Staff`; test a Viewer separately.
   Register remains `Marnisi M'Xlokk-MAIN`. Passwordless old login is unsupported.
6. Review catalogue VAT and prices. Sell Price is VAT-exclusive; the POS adds VAT
   once. Existing products initially become returnable; review `is_returnable`.
   Tailored Experience initially has variable pricing, 18% VAT and no returns.
   Repeated migrations preserve later catalogue choices; they do not run the
   separate price-update script.
7. Install the APK on a spare device, confirm the exact Connection settings URL,
   sign in and sync. Check the capability-fed tenders, catalogue, seller context
   and receipt labels before running the acceptance checklist below.

If migration fails, preserve its logs and backups; do not blindly revert source
or restore a pre-upgrade database after new transactions have been recorded.
The existing [recovery guide](FISCAL_ROLLOUT.md) covers reconciliation and rollback.


The frontend repository contains the full new-feature acceptance checklist in `docs/fiscal/SERVER_BUILD_20261008.md`. Protocol and recovery details are in [CHECKOUT_V2_ROLLOUT.md](CHECKOUT_V2_ROLLOUT.md).

The 8 October delivery verification passed 754 tests: 405 Flutter, 313 backend and 36 disposable-site integration cases (42 integration subchecks). No production migration or login was performed.
