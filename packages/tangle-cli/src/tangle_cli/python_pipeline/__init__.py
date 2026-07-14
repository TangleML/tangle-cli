"""Python-first authoring surface for Tangle pipelines.

End users write::

    from tangle_cli.python_pipeline import pipeline, task, registered, ref, raw, subpipeline, TaskEnv, In, Out

``cfg`` is NOT a top-level export — it is a parameter the framework
injects into the user's pipeline function at trace time. Importing the
:class:`tangle_cli.python_pipeline.cfg.Cfg` class is reserved for the
compile driver.

``import tangle_cli.python_pipeline`` is kept light: it does not
eagerly import the heavy ``tangle_cli.component_generator`` codegen
module or the tracer machinery.

Module map: authoring entry points live in :mod:`.pipeline`,
:mod:`.task`, :mod:`.subpipeline`, :mod:`.registered`, :mod:`.ref` and
:mod:`.raw`; the trace-time IR is built in :mod:`.trace` / :mod:`.graph`
and lowered to the dehydrated dict shape by :mod:`.emit`.
"""
from __future__ import annotations

from .pipeline import pipeline
from .raw import raw
from .ref import ref
from .registered import registered
from .subpipeline import subpipeline
from .task import task
from .task_env import TaskEnv
from .types import In, Out, Outputs

__all__ = [
    "pipeline",
    "task",
    "registered",
    "ref",
    "raw",
    "subpipeline",
    "TaskEnv",
    "In",
    "Out",
    "Outputs",
]
