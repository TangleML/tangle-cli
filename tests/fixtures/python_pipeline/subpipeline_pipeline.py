"""Fixture exercising subpipeline composition.

The parent pipeline embeds ``child_pipeline`` as a subpipeline. ``compile``
emits the child as its own sidecar under ``<stem>.subgraphs/`` and rewrites
the parent task's ``componentRef.url`` from the ``subpipeline://pending``
sentinel to a ``file://`` reference at that child sidecar. The child uses a
``@task`` so its own component sidecar is a hermetically-generated file (no
external relative componentRef to colocate).
"""
from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline, task


@task(image="python:3.12")
def child_task(greeting: str = "hi"):
    """Write a greeting.

    Metadata:
        Name: Child Task
    """
    print(greeting)


@pipeline("Child Pipeline")
def child_pipeline(seed: In[str], cfg) -> Out[str]:
    run_child_task = child_task(wait_for=seed)
    return run_child_task


@pipeline("Parent Pipeline")
def parent_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:
    return subpipeline(child_pipeline).named("Run Child")(seed=parent_wait_token)
