"""Fixture: a decorated task whose env is imported from a sibling module.

The sibling import is authoring-only. The runtime strip must drop that import
AND the decorator so the baked program does not crash with an import error (the
sibling module is not present in the runtime image). Authoring tokens are kept
out of this docstring on purpose so the strip test can substring-assert their
absence in the baked program.
"""
from cloud_pipelines import components
from task_env_strip_envs import UPI

from tangle_deploy.python_pipeline import task


@task(env=UPI)
def task_env_strip_imported(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Imported
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
