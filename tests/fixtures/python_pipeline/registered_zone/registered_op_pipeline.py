"""Fixture exercising ``@registered`` gen_config resolution.

``@registered`` references an EXISTING ``gen_config.yaml`` (generating no
sidecar of its own). At compile the task's ``componentRef`` is rewritten
from the ``registered://pending`` sentinel to a pure
``resolve://<rel>/gen_config.yaml#<fragment>`` URL, relative to the output
dir. With ``gen_config`` omitted, resolution falls back to the nearest
ancestor ``gen_config.yaml`` (this directory's) — the default OSS path,
requiring no zone-root marker.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, registered


@registered(fragment="run-query")
def run_query(sql_query: str = "SELECT 1") -> str:
    """Run a query.

    Metadata:
        Name: Run Query
        Version: 1.0.0
    """
    ...


@pipeline("Registered Pipeline")
def registered_pipeline(parent_wait_token: In[str]) -> Out[str]:
    run = run_query.named("Run Query")(wait_for=parent_wait_token)
    return run
