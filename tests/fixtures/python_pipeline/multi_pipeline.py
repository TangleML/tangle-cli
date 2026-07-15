"""Fixture with TWO ``@pipeline``-decorated functions.

Used to assert that compile fails cleanly when more than one pipeline is
found in a single file.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, ref


@pipeline("First Pipeline")
def first_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:
    wait_for_noop = ref(url="file://./noop.yaml")(wait_for=parent_wait_token)
    return wait_for_noop


@pipeline("Second Pipeline")
def second_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:
    wait_for_noop = ref(url="file://./noop.yaml")(wait_for=parent_wait_token)
    return wait_for_noop
