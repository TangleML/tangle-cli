"""Fixture: a co-located reusable env declaration feeding a decorated task.

The module-level env object exists ONLY to feed the decorator's env argument.
The runtime strip must drop the authoring import, the env declaration, AND the
decorator so the baked program does not crash referencing a stripped authoring
name at container start. (Tokens kept out of this docstring on purpose so the
strip test can substring-assert their absence in the baked program.)
"""
from cloud_pipelines import components

from tangle_deploy.python_pipeline import TaskEnv, task

UPI = TaskEnv(image="python:3.12")


@task(env=UPI)
def task_env_strip_colocated(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Colocated
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
