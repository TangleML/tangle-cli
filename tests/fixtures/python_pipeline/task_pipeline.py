"""Fixture using a ``@task``-decorated component.

``compile`` emits a sibling ``<stem>.components.yaml`` resolver sidecar
with one ``local_from_python`` entry for ``my_task`` and rewrites the
task's componentRef to a pure ``resolve://./<stem>.components.yaml#my-task``
URL. Hydrate regenerates the local component spec from this source file.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, task


@task(image="python:3.12")
def my_task(greeting: str = "hello"):
    """Write a greeting.

    Metadata:
        Name: My Task
    """
    print(greeting)


@pipeline("Task Pipeline")
def task_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:
    run_my_task = my_task(wait_for=parent_wait_token)
    return run_my_task
