"""In-memory IR for the traced pipeline graph.

The dataclasses here are the shared shapes used by the tracer
(``trace.py``) and the emitter (``emit.py``). Keeping them in their own
module avoids circular imports between those two.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class EdgeRef:
    """How one task's input wires to a producer.

    Either a taskOutput (depends on another task) or a graphInput (wired
    from a pipeline-level input).
    """

    kind: Literal["taskOutput", "graphInput"]
    task_id: str | None = None      # for taskOutput
    output: str | None = None       # for taskOutput
    input_name: str | None = None   # for graphInput


@dataclass
class TaskNode:
    """A single emitted task in the graph.

    ``arguments`` values may be plain strings, TaskOutputProxy objects
    (for taskOutput edges in non-``wait_for`` argument positions — not
    used in the PoC), or GraphInputPlaceholder objects.
    """

    task_id: str
    ref_url: str | None = None
    ref_name: str | None = None
    ref_digest: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    annotations: dict[str, str] | None = None


@dataclass
class GraphBuilder:
    """Holds the trace-time state for a single ``@pipeline`` body."""

    name: str
    description: str | None = None
    annotations: dict[str, str] = field(default_factory=dict)
    inputs: list[dict[str, Any]] = field(default_factory=list)
    outputs: list[dict[str, Any]] = field(default_factory=list)
    # MULTI-output map (Decision D): ``{output_name: EdgeRef}`` in field
    # declaration order, filled when the pipeline returns an ``Outputs``
    # typed object. ``emit_pipeline`` PREFERS this map when non-empty and
    # falls back to the single-output ``output_name``/``output_taskref``
    # compatibility shims below for the legacy ``-> Out[T]`` path.
    output_values: dict[str, EdgeRef] = field(default_factory=dict)
    # Single-output compatibility shims (legacy ``-> Out[T]`` path).
    output_name: str | None = None
    output_taskref: EdgeRef | None = None
    tasks: list[TaskNode] = field(default_factory=list)
    # AST pre-pass map filled in by ``trace.trace_pipeline`` so
    # ``CallableRef.__call__`` can derive task IDs from LHS variable
    # names. Keyed by ABSOLUTE source line number; values are the LHS
    # variable name (snake_case).
    lineno_to_lhs: dict[int, str] = field(default_factory=dict)
    # ``(task_id, CallableRef)`` tuples captured from
    # ``CallableRef.__call__`` when the ref was produced by ``@task``
    # (``_task_source_path`` is set). The compile driver uses this
    # list to (a) auto-emit a sibling ``<out>.components.yaml`` with one
    # ``local_from_python:`` entry per unique source file, and (b)
    # rewrite each task's ``componentRef.url`` to
    # ``resolve://./<out_stem>.components.yaml#<fragment>`` so hydrate
    # uses the hydrator's own ``local_from_python`` resolver.
    task_refs_for_local_from_python: list[tuple[str, Any]] = field(
        default_factory=list
    )
    # ``(task_id, CallableRef)`` tuples captured from
    # ``CallableRef.__call__`` when the ref was produced by ``@registered``
    # (``_registered_source_path`` is set). The compile driver uses this
    # list to rewrite each task's ``componentRef.url`` from the
    # ``registered://pending`` sentinel to a pure
    # ``resolve://<rel-path>/gen_config.yaml#<fragment>`` URL pointing at the
    # operation's EXISTING ``gen_config.yaml``. Unlike
    # ``task_refs_for_local_from_python``, this does NOT drive sidecar
    # generation -- the gen_config.yaml already exists on disk; the driver
    # only computes the relative ``resolve://`` URL (per artifact, against
    # that artifact's output dir) and stamps it onto the task.
    task_refs_for_registered: list[tuple[str, Any]] = field(
        default_factory=list
    )
    # ``(task_id, SubpipelineRef)`` tuples captured from
    # ``SubpipelineRef.__call__`` for every ``subpipeline(child)(...)`` call.
    # Recorded separately from ``@task`` refs so the compile driver can,
    # in a later phase, (a) compile each child ``PipelineFn`` to a graph
    # component sidecar and (b) rewrite the parent task's ``componentRef``
    # from the ``subpipeline://pending`` sentinel to a pure ``file://``
    # URL pointing at that sidecar. This list IS the explicit subpipeline
    # discriminator (Decision A) — the compiler never infers the kind
    # from ``ref_url is None``.
    task_refs_for_subpipelines: list[tuple[str, Any]] = field(
        default_factory=list
    )

    def add_task(self, node: TaskNode) -> None:
        """Append a task node in trace order."""
        self.tasks.append(node)
