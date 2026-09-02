"""Runnable Python-authoring example for task-level execution options.

Compile from the repository root with::

    uv run tangle sdk pipelines compile \
      examples/python_pipeline/execution_options_pipeline.py \
      --pipeline execution_options_pipeline \
      --output /tmp/tangle-execution-options-demo/pipeline.yaml
"""

from tangle_cli.python_pipeline import Out, pipeline, task


@task(image="python:3.12")
def read_runtime_state(name: str = "CREATED_BY") -> str:
    """Read state that changes between runs, so caching must be disabled."""
    import os

    return os.environ.get(name, "")


@task(image="python:3.12")
def flaky_upload(payload: str) -> str:
    """Stand-in for a task that benefits from retries."""
    print(payload)
    return payload


@pipeline("Execution options demo")
def execution_options_pipeline() -> Out[str]:
    # ``max_cache_staleness="P0D"`` is the narrow knob for the common
    # "never reuse a cached result for this task" case. Required whenever the
    # task reads runtime state that a cached result would silently stale out.
    runtime_state = read_runtime_state(
        name="CLOUD_PIPELINES_PIPELINE_RUN_CREATED_BY",
        max_cache_staleness="P0D",
    )

    # ``execution_options=`` is the general passthrough for the rest of
    # ExecutionOptionsSpec. Tangle models exactly two groups today,
    # ``cachingStrategy`` and ``retryStrategy``; anything else is rejected at
    # compile time because the backend would silently ignore it.
    uploaded = flaky_upload(
        payload=runtime_state.Output,
        execution_options={"retryStrategy": {"maxRetries": 3}},
    )
    return uploaded.Output
