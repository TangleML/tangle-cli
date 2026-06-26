"""Fixture: an env name referenced by the task body (invalid contract).

`UPI` is an authoring-only env declaration whose definition the strip removes,
but the task body also references `UPI` at runtime. Baking that would leave a
NameError at container start, so the strip must FAIL FAST (AuthoringStripError)
with guidance that env values are authoring-only.
"""
from cloud_pipelines import components

from tangle_deploy.python_pipeline import TaskEnv, task

UPI = TaskEnv(image="python:3.12")


@task(env=UPI)
def task_env_strip_body_ref(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Body Ref
        Version: 1.0.0
    """
    # Invalid: referencing the env object at runtime. Its declaration is
    # stripped, so this would be a NameError in the baked program.
    image = UPI.image
    with open(out, "w") as fh:
        fh.write(f"hi {who} on {image}")
