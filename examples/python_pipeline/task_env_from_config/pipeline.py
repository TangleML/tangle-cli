"""Runnable example: load a ``TaskEnv`` from a config file.

``TaskEnv.from_config`` declares the execution environment once, in
``envs.yaml``, instead of repeating an image string across tasks. The path is
relative to THIS file, so the same script picks the same config wherever
``tangle`` is run from.

Compile from the repository root with::

    uv run tangle sdk pipelines compile \
      examples/python_pipeline/task_env_from_config/pipeline.py \
      --pipeline scoring_pipeline \
      --output /tmp/tangle-task-env-from-config-demo/pipeline.yaml

The config uses ``_select`` over ``DEPLOY_ENVIRONMENT``; with the variable
unset the ``default`` case applies, so the command above works as written::

    DEPLOY_ENVIRONMENT=production uv run tangle sdk pipelines compile ...
"""

from tangle_cli.python_pipeline import Out, TaskEnv, pipeline, task

# One declaration, reused by every task below. Nothing about this is specific
# to a kind of environment: any ``TaskEnv`` dataclass subclass inherits
# ``from_config`` and is validated against its own fields.
SCORING = TaskEnv.from_config("envs.yaml")


@task(env=SCORING)
def load_queries(count: str = "3") -> str:
    """Produce the queries to score."""
    return ",".join(f"query-{index}" for index in range(int(count)))


@task(env=SCORING)
def score_queries(queries: str) -> str:
    """Score the queries on the same image, without repeating it."""
    return ";".join(f"{query}=1.0" for query in queries.split(","))


@pipeline("TaskEnv from config demo")
def scoring_pipeline() -> Out[str]:
    queries = load_queries(count="3")
    scored = score_queries(queries=queries.output)
    return scored.output
