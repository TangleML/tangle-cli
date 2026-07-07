"""Annotation markers for graph-level inputs and outputs.

``In[T]`` marks a parameter of a ``@pipeline`` function as a graphInput
(the pipeline's runtime parameter). ``Out[T]`` marks the return type
slot, which the framework binds to ``outputValues.<name>``.

These are pure annotation markers — no runtime behavior. The
``@pipeline`` decorator inspects ``fn.__annotations__`` to build the
``inputs:`` / ``outputs:`` blocks.
"""
from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class In(Generic[T]):
    """Mark a pipeline parameter as a graph input.

    Examples:
        def my_pipeline(parent_wait_token: In[str], cfg): ...
        def my_pipeline(threshold: In[int] = 5, cfg): ...
    """


class Out(Generic[T]):
    """Mark a pipeline return slot as a graph output.

    Example:
        def my_pipeline(cfg) -> Out[str]: ...
    """


class Outputs:
    """Base class for a typed MULTIPLE-output pipeline return object.

    A pipeline that exposes more than one named output declares a frozen
    dataclass subclass of :class:`Outputs` whose fields are annotated
    ``Out[T]``, and returns an instance of it::

        from dataclasses import dataclass
        from tangle_cli.python_pipeline import Out, Outputs, pipeline, ref

        @dataclass(frozen=True)
        class JudgeOutputs(Outputs):
            rows_written: Out[str]
            run_id: Out[str]

        @pipeline(name="Judge Options")
        def judge_options(input_table: In[str]) -> JudgeOutputs:
            judge = ref(url="file://./judge.yaml")(input_table=input_table)
            return JudgeOutputs(
                rows_written=judge.rows_written, run_id=judge.run_id
            )

    The tracer recognises an :class:`Outputs` subclass return annotation,
    reads its ``Out[T]`` fields (in declaration order), and emits one
    top-level ``outputs`` entry plus one ``implementation.graph.outputValues``
    entry per field. Each field value must be a task-output handle or an
    ``In[...]`` graph input (v1 does not support constant graph outputs).

    The existing single ``-> Out[T]`` path and ``@pipeline(output_name=...)``
    are unchanged; ``output_name`` may NOT be combined with an ``Outputs``
    return annotation (output names come from the fields).

    This is a pure marker base — it carries no runtime behavior; subclasses
    are ordinary (frozen) dataclasses.
    """
