# Marnisi fiscal controls

Implementation reference: Xirja backend `24c38fa` and ancestors; all runtime code
is owned by `xirja_marnisi`, without either Xirja backend dependency. Marnisi runs
Frappe 16.17.0/Python 3.14. Do not cherry-pick unrelated reference features.

Use this app's `CONTEXT_INDEX.md` first. The coordinated release documents live in
`XIRJA-frontend-marnisi/docs/fiscal/` in the same workspace:

- `ROLLOUT.md`: backups, additive migrations, own-password provisioning, coordinated
  Test rollout, Live approval gates, recovery suspension and rollback constraints.
- `TESTING.md`: single combined runner and explicitly selected disposable local site.
- `ANDROID_ACCEPTANCE.md`: physical Android/Bixolon installation, restart, Bluetooth,
  paper/printer failures, termination, offline recovery, duplicate-print exercises.
- `CONCERN_MATRIX.md`: all 60 original acceptance IDs across F1–F7, with explicit
  AX/loyalty/Windows/mixed-version/combined-exchange scope adaptations.
- `EXO_READINESS.md`: latest 502-test review, named case inventory, source/APK identity, corrected defects and certification gates. `evidence/RESULTS.md` retains the earlier implementation run.
- `SELECTIVE_COMMIT_MANIFEST.md`: review only these two Marnisi repositories; do not
  include unrelated uncommitted work or generated native/build caches.

## Backend deployment prerequisites

Back up and test restoration of database, public/private files and site encryption
configuration before running migrations. Run on a disposable staging clone first:
`bench --site <clone> migrate`, then repeat it and compare preserved payloads/prices.
The API never performs sales/history schema changes. Historical sales remain
uncertified. Catalogue `sell_price` remains VAT-exclusive; `vat_rate` starts at 18.

`Marnisi Fiscal Settings` starts Disabled. Test/Live fiscal counters are separate
per vineyard/year, with one record per payment event. Live requires verified seller,
VAT/address/EUR/EXO and all explicit approval/catalogue/tablet/queue/restore/physical/
outage confirmations. Setting a historical hard-coded EXO is not approval evidence.

Administrator-maintained `Marnisi Personal ID` is a unique Personal ID → User map.
Require that enabled user's own Frappe password, session/CSRF and locked vineyard
access. Keep individual credentials and the approved HTTPS origin on every tablet.
Expired sessions require explicit authentication; another operator cannot relabel
pending evidence. Viewers cannot sell, and staff cannot refund or link manuals.

Refunds are online and linked to original lines and payment events, with cumulative
quantity/value and tender limits. Arbitrary negative/mixed baskets are rejected.
Manual references are immutable and unique; no second electronic receipt is issued.
Reconciliation is read-only and includes legacy and exception sales.

Before restoring any older backup, suspend issuance via
`xirja_marnisi.api.fiscal.suspend_issuance` with an incident reference. Restore in
isolation and ensure the restored settings also remain suspended. Reconcile all
annual Test/Live high-watermarks against externally retained evidence and every
outbox. `resume_issuance` requires a separate approval reference and complete,
nondecreasing watermarks. Never infer missing issued numbers from a stale backup.

No commit, push, production deployment, credential rotation or Live enablement is
performed automatically. Physical and business/auditor acceptance remain required.
