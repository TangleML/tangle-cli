"""Two tasks forced to the SAME id via ``.named("Dup")``.

The emitter walks tasks in trace order and must reject a duplicate task
id explicitly rather than silently dropping the earlier task when the
tasks dict is built.
"""
from tangle_cli.python_pipeline import In, Out, pipeline, ref


@pipeline("Dup Task Pipeline")
def dup_task_pipeline(parent_wait_token: In[str], cfg) -> Out[str]:
    first = ref(url="file://./noop.yaml").named("Dup")(wait_for=parent_wait_token)
    second = ref(url="file://./noop.yaml").named("Dup")(wait_for=parent_wait_token)
    return second
