"""Fixture exercising compile-time ``cfg`` override on a subpipeline edge.

The child consumes ``cfg.message`` and emits it as a task-argument constant.
``.override_config(message=...)`` on the subpipeline edge sets the child's
COMPILE-TIME ``cfg`` value (a distinct namespace from ``.bind`` runtime
inputs), and the overridden value wins over the child's own ``config.yaml``.
The override is validated STRICT against the child's config keys.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline, task


@task(image="python:3.12")
def emit_task(message: str = "default"):
    """Emit a message constant.

    Metadata:
        Name: Emit Task
    """
    print(message)


@pipeline("Config Child", config="subpipeline_child_config.yaml")
def config_child(seed: In[str], cfg) -> Out[str]:
    run = emit_task(message=cfg.message, wait_for=seed)
    return run


@pipeline("Config Parent")
def config_parent(parent_wait_token: In[str], cfg) -> Out[str]:
    return (
        subpipeline(config_child)
        .named("Run Child")
        .override_config(message="from-override")(seed=parent_wait_token)
    )
