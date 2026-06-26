"""Fixture: an env import nested inside a block (FIX N2, §3.5).

`from task_env_strip_envs import UPI` lives inside an `if` block, so it is NOT a
direct child of the module body. Module-level removal only touches `tree.body`,
so this nested env import would NOT be stripped and would LEAK into the baked
runtime program -> ImportError at container start (the sibling authoring module
is not in the runtime image). Line-deleting the nested import is unsafe too
(it would leave an empty block -> IndentationError). The strip must therefore
FAIL FAST (AuthoringStripError) with guidance to move it to a top-level import.
"""
from cloud_pipelines import components

from tangle_deploy.python_pipeline import task

if True:
    from task_env_strip_envs import UPI


@task(env=UPI)
def task_env_strip_nested_import(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Nested Import
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
