"""Fixture: an inline reusable-env object constructed in the decorator argument.

The env object is constructed as a decorator argument, so the whole decorator
line range is deleted by the strip -- no residual env-construction text should
survive into the baked program. Authoring tokens are kept out of this docstring
on purpose so the strip test can substring-assert their absence in the baked
program.
"""
from cloud_pipelines import components

from tangle_deploy.python_pipeline import TaskEnv, task


@task(env=TaskEnv(image="python:3.12"))
def task_env_strip_inline(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Inline
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
