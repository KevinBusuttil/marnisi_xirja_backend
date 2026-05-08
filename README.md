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
