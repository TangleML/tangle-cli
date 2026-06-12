"""Fixture: a decorated task whose env is read via a sibling module import.

The strip collects the module-alias root from the decorator's env argument and
must drop the module import line AND the decorator, so the baked program does
not crash with an import error at container start. Authoring tokens are kept out
of this docstring on purpose so the strip test can substring-assert their
absence in the baked program.
"""
import task_env_strip_envs
from cloud_pipelines import components

from tangle_deploy.python_pipeline import task


@task(env=task_env_strip_envs.UPI)
def task_env_strip_module(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Module
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
