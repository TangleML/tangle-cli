"""Fixture: a decorated task with an explicit image override of its env.

An explicit image overrides the env's image (a Phase 2 sidecar concern). The
strip still has to drop the co-located env declaration, the authoring import,
and the whole decorator from the baked program -- including the override string,
which lives only inside the decorator. Authoring tokens are kept out of this
docstring on purpose so the strip test can substring-assert their absence in the
baked program.
"""
from cloud_pipelines import components

from tangle_deploy.python_pipeline import TaskEnv, task

UPI = TaskEnv(image="python:3.12")


@task(env=UPI, image="python:3.13-slim")
def task_env_strip_override(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Override
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
