"""Minimal Python-authored pipeline fixture for compile tests.

One task referencing a sibling component YAML by ``file://`` URL, wired
to a single graph input via ``wait_for`` and returned as the pipeline's
``Out[str]`` slot.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, ref


@pipeline("Noop Pipeline")
def noop_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:
    wait_for_noop = ref(url="file://./noop.yaml")(wait_for=parent_wait_token)
    return wait_for_noop
