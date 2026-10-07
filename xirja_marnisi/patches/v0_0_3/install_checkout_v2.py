"""Protocol 2 migration; repeatable, preserving configuration and old evidence."""
def execute():
    from xirja_marnisi.fiscal.migrations_v2 import ensure_schema
    ensure_schema()
