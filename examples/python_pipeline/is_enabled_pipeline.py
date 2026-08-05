"""Runnable Python-authoring example for task-level conditional execution.

Compile from the repository root with::

    uv run tangle sdk pipelines compile \
      examples/python_pipeline/is_enabled_pipeline.py \
      --pipeline conditional_pipeline \
      --output /tmp/tangle-is-enabled-demo/pipeline.yaml
"""

from tangle_cli.python_pipeline import In, Out, pipeline, task


@task(image="python:3.12")
def condition_value(value: str = "true") -> str:
    """Produce a string value that another task can use as its condition."""
    return value


@task(image="python:3.12")
def show_message(message: str) -> str:
    """Print and return a message when this task is enabled."""
    print(message)
    return message


@pipeline("Conditional execution demo")
def conditional_pipeline(enabled: In[str]) -> Out[str]:
    constant_false = show_message(
        message="This task is always skipped",
        is_enabled=False,
    )
    graph_input_condition = show_message(
        message="This task follows the runtime graph input",
        is_enabled=enabled,
    )
    computed_condition = condition_value(value="true")
    task_output_condition = show_message(
        message="This task follows another task's output",
        is_enabled=computed_condition.Output,
    )
    return task_output_condition.Output
