### XIRJA Marnisi

XIRJA Marnisi backend app

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch version-16
bench install-app xirja_marnisi
```

### Demo Data Seeding (Server)

Run these from your **bench root** where the site is installed.

1. Seed demo vineyards/users/items/packages from built-in source:

```bash
bash apps/xirja_marnisi/scripts/seed_demo_data.sh --site marnisi.local1
```

2. Seed demo data from a JSON export file:

```bash
bash apps/xirja_marnisi/scripts/seed_demo_data.sh \
  --site marnisi.local1 \
  --source-items-file /tmp/marsovin_items.json
```

3. Export Marsovin items from a source bench and seed target bench directly:

```bash
bash apps/xirja_marnisi/scripts/import_marsovin_seed.sh \
  /opt/frappe-bench-xirja xirja.local \
  /opt/frappebench-marnisi marnisi.local1 \
  120
```

The seeding endpoint used by the scripts is:
- `xirja_marnisi.api.seed.seed_demo_data`

### POS Prices and VAT

`Vineyard Item.sell_price` is VAT-exclusive. The POS bridge sends it unchanged
along with its currently configured `item_tax_pct = 18`. Flutter displays and
carts a unit price of `round(sell_price * 1.18, 2)`. It then separates the included
VAT for the subtotal/tax breakdown; it does not add another 18% at checkout.
For a desired VAT-inclusive price, divide by 1.18 and retain three decimals in
Sell Price. For example, `17.585` becomes EUR 20.75, consisting of EUR 17.58 net
and EUR 3.17 VAT for a single unit. Discounts, when enabled, reduce the inclusive
total before its VAT is calculated. This describes the app's current tax setup,
not a determination of the legally applicable rate for each product/service.

The requested Marnisi catalog prices are:

| Item | Sell Price (ex VAT) | POS Price (inc VAT) |
| --- | ---: | ---: |
| The Antonin Experience | 72.034 | 85.00 |
| The Terrarosa Experience | 84.746 | 100.00 |
| The Marnisi Wine Estate Experience | 97.458 | 115.00 |
| Antonin Blanc 0.75L | 17.585 | 20.75 |
| Antonin Blanc Rizerva | 47.669 | 56.25 |
| Antonin Noir 0.75L | 17.585 | 20.75 |
| Antonin Noir Rizerva 0.75L | 47.669 | 56.25 |
| Marsovin Brut Rose 0.75L (FMWCSR006, requested without gift box) | 25.932 | 30.60 |
| Cassar De Malte 0.75L | 25.339 | 29.90 |

For an explicit one-time update, run from the custom app root:

```bash
python3 scripts/update_pos_prices.py --base-url https://pos.example.com --personal-id YOUR_PERSONAL_ID
```

Replace the example with the approved HTTPS origin. The script prompts for your
own Frappe password, uses the server CSRF token, and refuses redirects. Personal
ID alone no longer authenticates. This is a dry run. Add `--apply --backup-file /absolute/path/to/new-price-backup.json`
only when ready to update that backend. It matches exact names/codes in
`Marnisi M'Xlokk`, checks the current VAT rate, backs up the original documents,
and updates only Sell Price through Frappe's authenticated resource API with
stale-document checks. No credentials/cookies are written to the backup.
The script verifies the resulting POS feed, unchanged stock/images, and prices
of untargeted items. It is not registered in seed scripts, hooks or migrations.
Updates are per-document transactions, not a single batch transaction. If an
update fails, inspect the reported successful rows and the backup before retrying;
do not blindly restore documents over subsequent sales or stock changes.

In the app, use **Settings > General Settings > Save** to sync the new prices,
then re-enter **Sales > Register**. Existing cart lines and historical sales are
not repriced; use a new order to verify the new catalog prices.

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/xirja_marnisi
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

mit


### Fiscal controls

See [FISCAL_ROLLOUT.md](FISCAL_ROLLOUT.md) and [CONTEXT_INDEX.md](CONTEXT_INDEX.md)
for the Marnisi-owned implementation, Frappe16 migrations, personal-ID mapping,
combined test runner and coordinated Android acceptance gates. Fiscal migration
never runs the price updater or converts existing `sell_price` values.
