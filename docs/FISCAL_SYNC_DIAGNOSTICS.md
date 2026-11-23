# Fiscal upload diagnostics

Private rotating log: `sites/<site>/logs/marnisi_fiscal_sync.log`, with a bench-level copy under `logs/`. Existing tablets can retry their original saved sales without an APK update. Logging does not repair pending sales or relax validation.

## One diagnostic pass

1. Deploy diagnostics and restart the correct bench. Inspect production edits first; never reset production changes or fiscal data.
2. The original cashier retries synchronization once on the tablet holding the saved sale. Do not create another order or collect payment again.
3. Inspect entries for that sale number, including rotated files. Correlate events using `attempt_id`; automatic batch retries have distinct IDs and a `batch_attempt` number.
4. Use the saved IDs and captured catalogue rows to inspect the current backend catalogue. Do not invent aliases or replace frozen IDs based on an unverified name match.

## Rejection details

Schema 2 records the validation stage, authenticated cashier, frozen line/payment/tax/discount figures, printed snapshot projection, protocol/store/register, fiscal revisions and settings readiness, existing-sale hashes, and checkout-authorization presence/hash/owner/mode facts. Catalogue diagnostics contain the actual requested IDs, scoped rows returned by the validator, and active failing item ID. It issues no additional diagnostic SQL or locks.

Unexpected exceptions retain only their type, numeric database error code when present, and file/function/line locations. No exception SQL, source lines or locals are copied. Names, descriptions and seller text are fingerprinted. Description flags distinguish controls/markup. Explicit schema projections omit passwords, cookies, session IDs, authorization tokens and unrelated fields. Arrays retain up to the contract maximum of 500 entries, report counts and flag truncation. Logs rotate at 2 MiB with 10 retained files.

## Outcomes and limits

- `sale_upload_rejected`: a validation rejection or retryable database conflict; API behavior is unchanged.
- `sale_acknowledgement_generated`: acknowledgement produced, awaiting outer request commit. Review outcomes include reasons and diagnostic facts.
- `sale_request_committed`: database after-commit hook ran. This does not prove the tablet received or accepted the HTTP response.

Batch-shape and application authorization failures are logged while preserving exceptions. Generic Frappe authentication/CSRF rejection, proxy failures and connection loss before endpoint execution require existing web/proxy/request logs. Logs cannot reconstruct earlier rejected uploads.

Logger failures cannot determine fiscal status or write/commit financial rows. Tests run through the sibling frontend `./run_all_tests.sh`; physical tablet/printer acceptance remains separate.
