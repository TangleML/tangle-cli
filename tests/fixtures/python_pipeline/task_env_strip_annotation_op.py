"""Fixture: a co-located env used ONLY in a return type annotation (FIX N1).

The module-level env object exists ONLY to feed the decorator's env argument and
is ALSO referenced as a return type annotation, but NEVER in the function body.
Type annotations are removed from the baked program by a later type-hint pass,
so an annotation-only reference must not be mistaken for a live runtime
reference and must not trip the "still referenced by kept code" fail-fast. The
runtime strip must drop the authoring import, the env declaration, the
decorator, AND the annotation, so the baked program is env-free and runs without
a NameError. (Authoring tokens kept out of this docstring on purpose so the
strip test can substring-assert their absence in the baked program.)
"""
from cloud_pipelines import components

from tangle_deploy.python_pipeline import TaskEnv, task

UPI = TaskEnv(image="python:3.12")


@task(env=UPI)
def task_env_strip_annotation(out: components.OutputPath("Text"), who: str = "world") -> UPI:
    """
    Metadata:
        Name: Task Env Strip Annotation
        Version: 1.0.0
    """
    with open(out, "w") as fh:
        fh.write(f"hi {who}")
