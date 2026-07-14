"""``subpipeline(child_pipeline)`` — nested-pipeline authoring handle.

A parent ``@pipeline`` uses another Python ``@pipeline`` as a graph
operation by wrapping it::

    from tangle_cli.python_pipeline import subpipeline

    child_result = subpipeline(child_pipeline).named("Run Child")(
        required_input=upstream.some_output,
        depends_on=upstream,
    )

``subpipeline(child)`` returns a :class:`SubpipelineRef` — a task-like
handle that mirrors :class:`tangle_cli.python_pipeline.ref.CallableRef`
ergonomics (``.bind`` / ``.named`` / ``.with_annotations`` and call-site
kwargs). Calling the handle inside an active ``@pipeline`` trace records
ONE parent task (never the child's internals) and returns a
:class:`tangle_cli.python_pipeline.placeholders.TaskOutputProxy`.

The child body is NOT executed into the parent's :class:`GraphBuilder`.
The compile driver (a later milestone) reads the recorded child
``PipelineFn`` from ``builder.task_refs_for_subpipelines`` to compile it
into a graph component sidecar and rewrite the parent task's
``componentRef`` from the :data:`_SUBPIPELINE_URL_PLACEHOLDER` sentinel
to a pure ``file://`` ref. Per Decision A the subpipeline kind is carried
explicitly (this dedicated handle + the separate builder registry); it is
never inferred from ``ref_url is None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from .errors import CompileError

if TYPE_CHECKING:  # pragma: no cover
    from .pipeline import PipelineFn
    from .placeholders import TaskOutputProxy


# Transient componentRef URL stamped on a subpipeline task during trace.
# The compile driver rewrites it to a pure ``file://./<stem>.subgraphs/
# <child>.yaml`` URL after compiling the child. It is its OWN sentinel,
# distinct from ``@task``'s ``local-from-python://pending``, so a missed
# rewrite can fail with a subpipeline-specific error rather than being
# confused with a ``@task`` placeholder. It must never reach written output.
_SUBPIPELINE_URL_PLACEHOLDER = "subpipeline://pending"


@dataclass(frozen=True)
class SubpipelineRef:
    """Immutable, task-like handle wrapping a child :class:`PipelineFn`.

    Mirrors :class:`CallableRef`'s combinators and trace-time ``__call__``
    but carries an explicit ``child`` ``PipelineFn`` instead of a
    component URL. The wrapped child is the source of truth for the
    child's declared inputs/outputs in later compile phases.
    """

    child: "PipelineFn"
    bound_kwargs: dict[str, Any] = field(default_factory=dict)
    task_id_hint: str | None = None
    annotations: dict[str, str] | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Builders — all return a fresh SubpipelineRef (immutable composition)

    def bind(self, **kwargs: Any) -> "SubpipelineRef":
        """Return a new handle with ``kwargs`` merged into ``bound_kwargs``.
        Later ``.bind`` calls win on conflict."""
        merged = {**self.bound_kwargs, **kwargs}
        return replace(self, bound_kwargs=merged)

    def named(self, task_id: str) -> "SubpipelineRef":
        """Return a new handle whose parent task ID will be ``task_id``
        instead of the LHS-derived auto ID."""
        return replace(self, task_id_hint=task_id)

    def with_annotations(self, ann: dict[str, Any]) -> "SubpipelineRef":
        """Return a new handle with per-task annotations applied to the
        PARENT subpipeline task (not the child sidecar's metadata)."""
        merged: dict[str, str] = dict(self.annotations or {})
        for k, v in ann.items():
            merged[k] = v  # type: ignore[assignment]
        return replace(self, annotations=merged)

    def override_config(self, **kwargs: Any) -> "SubpipelineRef":
        """Return a new handle with compile-time cfg overrides for the direct
        child merged in (later calls win on conflict).

        A DISTINCT namespace from :meth:`bind`: ``.bind(...)`` sets the
        child's RUNTIME ``In[...]`` graph-input bindings, whereas
        ``.override_config(...)`` sets the child's COMPILE-TIME ``cfg``
        values. The keyword is the CHILD's config key (so a value can be
        remapped, e.g. ``standardized_options_model=cfg.pvso_output_table_name``)
        and the value is any already-typed Python expression. These are
        validated STRICT against the child's ``config.yaml`` in the compile
        driver — an unknown key is a compile error (typo protection)."""
        merged = {**self.config_overrides, **kwargs}
        return replace(self, config_overrides=merged)

    # ------------------------------------------------------------------
    # Trace-mode call site

    def __call__(self, **kwargs: Any) -> "TaskOutputProxy":
        """Trace-mode invocation.

        Records a :class:`TaskNode` for the nested-pipeline boundary into
        the active :class:`GraphBuilder`, registers ``(task_id, self)`` in
        ``builder.task_refs_for_subpipelines``, and returns a bare
        :class:`TaskOutputProxy` handle. The task ID is derived exactly as
        :meth:`CallableRef.__call__` does — ``.named(...)`` wins, else the
        single-Name LHS at the call site.

        The child ``PipelineFn`` is reachable for later compile phases via
        the builder registry (``self.child``); the returned proxy is a
        strict :class:`SubpipelineOutputProxy` that knows the child's
        declared outputs (derived from the child's return annotation) so
        unknown named access fails early and a bare proxy resolves to a
        default output only when unambiguous.
        """
        import sys

        from . import trace
        from .errors import AmbiguousTaskIdError
        from .graph import TaskNode
        from .ids import snake_to_title_case
        from .placeholders import SubpipelineOutputProxy

        builder = trace.current_builder()
        if builder is None:
            raise RuntimeError(
                "subpipeline(child)(...) requires an active @pipeline trace "
                "context. Either call this inside a function decorated with "
                "@pipeline, or compile the script with `tangle sdk pipelines compile`."
            )

        # Resolve the parent task ID. ``.named(...)`` always wins over the
        # AST-derived auto ID.
        if self.task_id_hint is not None:
            task_id = self.task_id_hint
        else:
            caller_lineno = sys._getframe(1).f_lineno
            lhs_name = builder.lineno_to_lhs.get(caller_lineno)
            if lhs_name is None:
                raise AmbiguousTaskIdError(
                    f"Cannot infer task ID at line {caller_lineno}: the LHS "
                    "is not a single bare variable name. Either bind to a "
                    "single name (e.g. `child = subpipeline(...)(...)`) or "
                    "call `.named('Task Id')` on the subpipeline handle."
                )
            task_id = snake_to_title_case(lhs_name)

        # Merge bound and call-site kwargs: call-site keys come first in
        # insertion order and win on conflict; bound keys are appended.
        merged: dict[str, Any] = {}
        for k, v in kwargs.items():
            merged[k] = v
        for k, v in self.bound_kwargs.items():
            if k not in merged:
                merged[k] = v

        node = TaskNode(
            task_id=task_id,
            ref_url=_SUBPIPELINE_URL_PLACEHOLDER,
            arguments=merged,
            annotations=dict(self.annotations) if self.annotations else None,
        )
        builder.add_task(node)
        builder.task_refs_for_subpipelines.append((task_id, self))

        # Derive the child's declared outputs statically so the returned
        # proxy can reject unknown named access and resolve bare references
        # per the default-output rule (Decision E). ``None`` => undeterminable
        # => permissive base behavior.
        declared = trace.declared_output_names(self.child)
        return SubpipelineOutputProxy(
            task_id=task_id,
            declared_outputs=declared,
            child_name=self.child.name,
        )


def subpipeline(child: "PipelineFn") -> SubpipelineRef:
    """Wrap a child ``@pipeline`` as a task-like nested-pipeline handle.

    Args:
        child: The child ``PipelineFn`` to compile as a subgraph. Must be
            a ``@pipeline``-decorated function imported from another module.

    Returns:
        A :class:`SubpipelineRef` supporting ``.bind`` / ``.named`` /
        ``.with_annotations`` and trace-time ``__call__``.

    Raises:
        CompileError: if ``child`` is not a :class:`PipelineFn` (e.g. a
            plain function, a :class:`CallableRef`, or anything else).
    """
    # Local import to avoid an import cycle at package load time.
    from .pipeline import PipelineFn

    if not isinstance(child, PipelineFn):
        raise CompileError(
            "subpipeline(...) expects a @pipeline-decorated function "
            f"(PipelineFn), got {type(child).__name__!r}. Import a child "
            "@pipeline and wrap it, e.g. subpipeline(child_pipeline)(...). "
            "For non-Python or precompiled components use ref(url=...)."
        )
    return SubpipelineRef(child=child)
