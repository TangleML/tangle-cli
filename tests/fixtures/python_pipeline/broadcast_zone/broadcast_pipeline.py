"""Fixture: ``propagate_config`` broadcast through a config-less intermediate.

The root sets ``propagate_config=True`` and declares ``shared_key`` in its own
config.yaml. ``Broadcast Middle`` is a pure composer — no ``cfg`` parameter and
no ``config=``, so it has NO config.yaml on disk. ``Broadcast Grandchild``
declares ``shared_key`` in its own config and emits it as a task-argument
constant.

The broadcast must flow PAST the config-less ``Middle`` and reach
``Grandchild``, where the root's broadcast value wins over the grandchild's own
config.yaml value (same key name). This is the canonical nesting shape; before
the broadcast path tolerated a missing intermediate config.yaml it hard-failed
here with a spurious "config file not found".
"""
from tangle_cli.python_pipeline import In, Out, pipeline, subpipeline, task


@task(image="python:3.12")
def leaf_task(shared_key: str = "from-task-default"):
    """Emit the broadcast key.

    Metadata:
        Name: Leaf Task
    """
    print(shared_key)


@pipeline("Broadcast Grandchild", config="broadcast_grandchild_config.yaml")
def broadcast_grandchild(seed: In[str], cfg) -> Out[str]:
    run = leaf_task(shared_key=cfg.shared_key, wait_for=seed)
    return run


@pipeline("Broadcast Middle")  # config-less composer: no cfg param, no config=
def broadcast_middle(seed: In[str]) -> Out[str]:
    return subpipeline(broadcast_grandchild).named("Run Grandchild")(seed=seed)


@pipeline("Broadcast Root", config="broadcast_root_config.yaml", propagate_config=True)
def broadcast_root(parent_wait_token: In[str], cfg) -> Out[str]:
    return subpipeline(broadcast_middle).named("Run Middle")(seed=parent_wait_token)
