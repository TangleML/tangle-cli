"""Fixture exercising ``@registered`` with an EXPLICIT RELATIVE gen_config.

A relative ``gen_config`` is resolved against the nearest ancestor holding a
registered ``ZONE_ROOT_MARKERS`` marker (the "zone root"). In the default
open-source build ``ZONE_ROOT_MARKERS`` is empty, so a relative path is
REJECTED with an actionable error; a downstream distribution that carries a
zone concept appends its marker filename to restore resolution.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, registered


@registered(fragment="run-query", gen_config="gen_config.yaml")
def run_query_rel(sql_query: str = "SELECT 1") -> str:
    """Run a query.

    Metadata:
        Name: Run Query
        Version: 1.0.0
    """
    ...


@pipeline("Registered Relative Pipeline")
def registered_relative_pipeline(parent_wait_token: In[str]) -> Out[str]:
    run = run_query_rel.named("Run Query")(wait_for=parent_wait_token)
    return run
