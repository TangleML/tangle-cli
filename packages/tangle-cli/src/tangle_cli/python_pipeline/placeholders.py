"""Trace-time placeholder objects.

These objects are returned by the framework's primitives during trace
mode. They are NOT executed — they only carry enough metadata for
``emit.py`` to render the corresponding YAML sub-tree.

Two kinds:

* :class:`TaskOutputProxy` — returned by ``CallableRef.__call__``.
  Acts as a handle to a task's output. Bare proxies (``_output is None``)
  emit ``outputName: wait_for_output`` (the canonical done sentinel).
  Attribute access (``task_a.rows_written``) returns a NEW proxy with
  ``_output="rows_written"`` so emit can pick the named output.

* :class:`SubpipelineOutputProxy` — a STRICT :class:`TaskOutputProxy`
  returned by ``subpipeline(child)(...)``. It knows the child's declared
  output names (derived at parent trace time from the child
  ``PipelineFn``) and rejects unknown named access; a bare proxy resolves
  to a default output only when unambiguous.

* :class:`GraphInputPlaceholder` — bound to each ``In[T]`` parameter at
  trace start. When passed as ``wait_for=cfg.parent_wait_token`` (or
  ``depends_on=...``) it emits as ``graphInput: {inputName: <name>}``.
  When passed as a regular argument it emits as a ``graphInput`` value
  too (dispatch is purely on value type in ``emit.py``).
"""
from __future__ import annotations


class TaskOutputProxy:
    """Handle to a task's output.

    Construction: ``TaskOutputProxy(task_id="Build Quality Tables")`` is
    a bare proxy (``_output is None`` → emit defaults to
    ``wait_for_output``). Attribute access ``proxy.rows_written`` yields
    a new proxy with the output name pinned.

    Note: ``__getattr__`` is only called when normal lookup fails. The
    private attrs ``_task_id`` / ``_output`` are set on the instance so
    they go through normal lookup; user-facing names (``rows_written``,
    ``foo``, ...) fall through to ``__getattr__``.
    """

    __slots__ = ("_task_id", "_output")

    def __init__(self, task_id: str, output: str | None = None) -> None:
        self._task_id = task_id
        self._output = output

    def __getattr__(self, name: str) -> "TaskOutputProxy":
        # Names starting with `_` are framework-internal — treat as
        # missing rather than silently wrapping; surfaces typos cleanly.
        if name.startswith("_"):
            raise AttributeError(name)
        return TaskOutputProxy(self._task_id, name)

    def _resolved_output_name(self) -> str:
        """Effective output name for emission.

        A bare proxy (``_output is None``) defaults to the canonical
        ``wait_for_output`` done sentinel — the permissive behavior used by
        generic ``ref()`` / ``@task`` outputs. :class:`SubpipelineOutputProxy`
        overrides this to apply child-declared-output rules.
        """
        return self._output or "wait_for_output"

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"TaskOutputProxy(task_id={self._task_id!r}, output={self._output!r})"


class SubpipelineOutputProxy(TaskOutputProxy):
    """Strict output handle for a ``subpipeline(child)(...)`` boundary task.

    Unlike the permissive base proxy, this proxy KNOWS the child's declared
    output names — derived at parent trace time from the child
    ``PipelineFn``'s return annotation (a single ``-> Out[T]`` yields the
    one ``output_name``; an ``Outputs`` subclass yields its field names) —
    and enforces them:

    * attribute access for an output NOT in the declared set raises a clear
      :class:`CompileError` listing the declared outputs;
    * a bare proxy (``_output is None``) used as a dependency
      (``wait_for=child`` / ``depends_on=child``) resolves to a default
      output only when unambiguous — a single declared output, or an output
      literally named ``wait_for_output``; otherwise it raises.

    ``declared_outputs is None`` means the child's outputs could not be
    determined statically (an unsupported return shape); the proxy then
    degrades to the permissive base behavior so authoring is never blocked
    by a best-effort gap.
    """

    __slots__ = ("_declared_outputs", "_child_name")

    def __init__(
        self,
        task_id: str,
        declared_outputs: tuple[str, ...] | None,
        child_name: str,
        output: str | None = None,
    ) -> None:
        super().__init__(task_id, output)
        self._declared_outputs = declared_outputs
        self._child_name = child_name

    def __getattr__(self, name: str) -> "TaskOutputProxy":
        if name.startswith("_"):
            raise AttributeError(name)
        declared = self._declared_outputs
        if declared is not None and name not in declared:
            from .errors import CompileError

            raise CompileError(
                f"child pipeline {self._child_name!r} has no output {name!r}. "
                f"Declared outputs: {list(declared)}."
            )
        return SubpipelineOutputProxy(
            self._task_id, declared, self._child_name, output=name
        )

    def _resolved_output_name(self) -> str:
        if self._output is not None:
            return self._output
        declared = self._declared_outputs
        # Permissive fallback when the child interface is undeterminable.
        if declared is None:
            return "wait_for_output"
        if len(declared) == 1:
            return declared[0]
        if "wait_for_output" in declared:
            return "wait_for_output"
        from .errors import CompileError

        raise CompileError(
            f"child pipeline {self._child_name!r} declares multiple outputs "
            f"{list(declared)} and none named 'wait_for_output', so a bare "
            "reference is ambiguous. Access a named output explicitly, e.g. "
            f"`<task>.{declared[0]}`."
        )

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return (
            f"SubpipelineOutputProxy(task_id={self._task_id!r}, "
            f"output={self._output!r}, declared={self._declared_outputs!r})"
        )


class GraphInputPlaceholder:
    """Trace-time stand-in for a pipeline ``In[T]`` parameter.

    Carried verbatim through trace; ``emit.py`` decides how to render it
    based on the argument key (``wait_for`` / ``depends_on`` → graphInput
    sub-dict; otherwise bare input name).
    """

    __slots__ = ("input_name",)

    def __init__(self, input_name: str) -> None:
        self.input_name = input_name

    def __repr__(self) -> str:  # pragma: no cover — debug only
        return f"GraphInputPlaceholder(input_name={self.input_name!r})"
