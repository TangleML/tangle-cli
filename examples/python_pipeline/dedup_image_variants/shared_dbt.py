"""One shared dbt-style helper, imported and re-decorated by ``pipeline.py``.

This module exists so the example exercises the REAL shape of the bug: a single
module-qualified function (``dedup_image_variants.shared_dbt.run_dbt``) reached
from several call sites with different task-level images, rather than two
separate functions that happen to look alike.
"""


def run_dbt(model: str, target: str = "prod") -> str:
    """Run one dbt model.

    Metadata:
        Name: Run Dbt Model
    """
    return f"{model}@{target}"
