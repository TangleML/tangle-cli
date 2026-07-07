"""Fixture with NO ``@pipeline``-decorated function.

Used to assert that compile fails cleanly when no pipeline is found.
"""
from tangle_cli.python_pipeline import ref

# A plain ref is not a PipelineFn, so discovery must find zero pipelines.
some_ref = ref(url="file://./noop.yaml")
