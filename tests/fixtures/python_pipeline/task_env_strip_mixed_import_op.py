"""Fixture: an env-only name sharing an import line with a used runtime name.

`from task_env_strip_envs import UPI, helper` mixes an authoring-only env name
(UPI) with a runtime helper that the body actually calls. The strip cannot
line-delete only part of the statement, so it must FAIL FAST (AuthoringStripError)
with guidance to split the import -- never bake a likely-broken
`from task_env_strip_envs import UPI` line into the runtime program.
"""
from cloud_pipelines import components
from task_env_strip_envs import UPI, helper

from tangle_deploy.python_pipeline import task


@task(env=UPI)
def task_env_strip_mixed_import(out: components.OutputPath("Text"), who: str = "world"):
    """
    Metadata:
        Name: Task Env Strip Mixed Import
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(helper(who))
