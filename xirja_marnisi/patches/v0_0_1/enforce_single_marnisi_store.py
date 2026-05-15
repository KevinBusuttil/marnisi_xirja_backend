from __future__ import annotations

from xirja_marnisi.api import seed


def execute() -> None:
    seed._enforce_single_store_setup(deactivate_other_vineyards=True)
