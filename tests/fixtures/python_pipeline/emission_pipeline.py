"""Compile fixture for native ``.with_emission(...)`` readiness annotations.

A container task and a subpipeline boundary task each announce a readiness
event; the container task also carries an unrelated annotation so the compiled
output shows both surviving side by side.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, ref, subpipeline, task

EXPORTER = ref(name="exporter")


@task(image="python:3.12")
def child_task(greeting: str = "hi"):
    """Write a greeting.

    Metadata:
        Name: Child Task
    """
    print(greeting)


@pipeline("Emission Child")
def child_pipeline(seed: In[str]) -> Out[str]:
    run_child_task = child_task(wait_for=seed)
    return run_child_task


@pipeline("Emission Parent")
def parent_pipeline(seed: In[str]) -> Out[str]:
    EXPORTER.with_annotations({"team": "orders"}).with_emission("orders-ready").named(
        "Export Orders"
    )(wait_for=seed)
    run_child = subpipeline(child_pipeline).with_emission("child-ready").named("Run Child")(
        seed=seed
    )
    return run_child
