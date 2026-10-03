"""``@Layout()`` — compile-time graph auto-layout for a pipeline definition.

``Layout`` asks the compiler to auto-layout a graph, optionally with a named
algorithm::

    from tangle_cli.python_pipeline import Layout, pipeline

    @Layout("banded")                    # this graph and its subgraphs
    @pipeline("Daily Pulse")
    def daily_pulse(...): ...

    @Layout(recursive=False)             # this graph only
    @pipeline("Judge")
    def judge(...): ...

Both stacking orders around ``@pipeline`` are supported; the decorator
returns its target unchanged. This package ships no layout algorithm: the
caller of :func:`tangle_cli.pipeline_compiler.compile_pipeline` passes a
:class:`GraphLayoutTransform`, which receives each covered graph body and
returns it with ``editor.position`` annotations. Without a transform the
request has no effect on output (the compiler emits a warning).

Validation here is structural only: ``algorithm`` must be ``None`` or an
exact, non-empty ``str``, and ``recursive`` an exact ``bool``. Which names
are supported, and what ``None`` (the default) means, is the transform's
decision. Error messages never render the rejected value or target.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from .errors import InvalidLayoutError

_T = TypeVar("_T")

# Private marker for the ``@pipeline`` -over- ``@Layout()`` order: the inner
# ``Layout`` stamps the plain function, and ``@pipeline`` snapshots it onto
# the ``PipelineFn`` it builds (see :func:`layout_marker_of`).
_LAYOUT_MARKER = "__tangle_layout__"

_BARE_OR_POSITIONAL = (
    "Layout must be called, with at most one positional algorithm name: use "
    '@Layout(), @Layout("<name>") or @Layout(algorithm="<name>", recursive=...).'
)
_BAD_ALGORITHM = "Layout(algorithm=...) must be None or a non-empty str."
_BAD_RECURSIVE = "Layout(recursive=...) must be True or False."
_BAD_TARGET = (
    "@Layout() decorates a graph definition only: a @pipeline function, or a "
    "plain function that @pipeline then wraps. Task, subpipeline and other "
    "objects are not supported; decorate the child @pipeline definition instead."
)
_DUPLICATE = (
    "this pipeline definition already has a @Layout(); apply @Layout() at "
    "most once per graph definition."
)


class Layout:
    """Immutable request to auto-layout a graph definition at compile time.

    Args:
        algorithm: Named layout algorithm, or ``None`` for the transform's
            default. May be given positionally (an exact ``str`` only) or by
            keyword. A bare ``@Layout`` without parentheses is refused.
        recursive: When ``True`` (the default) the request also covers every
            undecorated descendant subgraph. ``False`` covers only this
            graph. A descendant with its own ``@Layout()`` always uses its
            own declaration, and its own ``recursive`` governs below it.
    """

    __slots__ = ("_algorithm", "_recursive")

    _algorithm: str | None
    _recursive: bool

    def __init__(self, *args: Any, algorithm: str | None = None, recursive: bool = True) -> None:
        if args:
            # A bare ``@Layout`` passes its target positionally; only one
            # exact-``str`` positional (the algorithm) is accepted, and only
            # without the keyword. The argument is never inspected or rendered.
            if len(args) != 1 or type(args[0]) is not str or algorithm is not None:
                raise InvalidLayoutError(_BARE_OR_POSITIONAL)
            algorithm = args[0]
        if algorithm is not None and (type(algorithm) is not str or not algorithm):
            raise InvalidLayoutError(_BAD_ALGORITHM)
        if type(recursive) is not bool:
            raise InvalidLayoutError(_BAD_RECURSIVE)
        object.__setattr__(self, "_algorithm", algorithm)
        object.__setattr__(self, "_recursive", recursive)

    @property
    def algorithm(self) -> str | None:
        """The requested algorithm name, or ``None`` for the transform's default."""
        return self._algorithm

    @property
    def recursive(self) -> bool:
        """Whether undecorated descendant subgraphs are covered too."""
        return self._recursive

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("Layout is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("Layout is immutable")

    def __eq__(self, other: object) -> bool:
        if type(other) is not Layout:
            return NotImplemented
        return (self._algorithm, self._recursive) == (other._algorithm, other._recursive)

    def __hash__(self) -> int:
        return hash((Layout, self._algorithm, self._recursive))

    def __repr__(self) -> str:
        parts = []
        if self._algorithm is not None:
            parts.append(f"algorithm={self._algorithm!r}")
        if not self._recursive:
            parts.append("recursive=False")
        return f"Layout({', '.join(parts)})"

    def __reduce__(self) -> tuple[Any, ...]:
        return (_rebuild_layout, (self._algorithm, self._recursive))

    def __call__(self, target: _T) -> _T:
        # Local import: ``pipeline`` imports this module at load time.
        from .pipeline import PipelineFn

        # Exact types only, so no author-defined attribute or container hook
        # runs: subclasses of PipelineFn are refused (as the deployment
        # decorators do), and a function's ``__dict__`` is accessed through
        # unbound ``dict`` methods in case it was replaced by a dict subclass.
        target_type = type(target)
        if target_type is PipelineFn:
            if target.layout is not None:  # type: ignore[attr-defined]
                raise InvalidLayoutError(_DUPLICATE)
            target.layout = self  # type: ignore[attr-defined]
            return target
        if target_type is types.FunctionType:
            namespace = target.__dict__  # type: ignore[attr-defined]
            if dict.__contains__(namespace, _LAYOUT_MARKER):
                raise InvalidLayoutError(_DUPLICATE)
            dict.__setitem__(namespace, _LAYOUT_MARKER, self)
            return target
        raise InvalidLayoutError(_BAD_TARGET)


def _rebuild_layout(algorithm: str | None, recursive: bool = True) -> Layout:
    return Layout(algorithm=algorithm, recursive=recursive)


def layout_marker_of(fn: Any) -> Layout | None:
    """Return the ``@Layout()`` stamped directly on plain function ``fn``.

    Only exact Python functions are read, through their own ``__dict__`` via
    unbound ``dict`` methods, so no author-defined attribute or container
    hook runs. A marker that is not a genuine :class:`Layout` is ignored.
    """
    if type(fn) is not types.FunctionType:
        return None
    marker = dict.get(fn.__dict__, _LAYOUT_MARKER)
    return marker if type(marker) is Layout else None


@dataclass(frozen=True)
class TaskInterface:
    """Ordered port names of one graph task, for layout geometry only.

    Attributes:
        inputs: Input names: the component's declared inputs in declaration
            order, followed by any other argument the task supplies.
        outputs: Output names: declared outputs in declaration order,
            followed by any other output consumed in the graph.
        approximate: ``True`` when the component interface is not known
            locally (``ref`` / ``@registered`` components): the inputs are
            then every argument the task supplies (edges and filled literal
            values) and the outputs those consumed elsewhere in the graph, so
            the real card may be larger. ``False`` for ``@task`` and ``subpipeline`` tasks.
    """

    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    approximate: bool = False


@dataclass(frozen=True)
class GraphLayoutContext:
    """What a :class:`GraphLayoutTransform` knows about the graph it lays out.

    Attributes:
        layout: The governing :class:`Layout` (this graph's own declaration,
            or the nearest recursive ancestor's).
        path: Task-ID path from the root graph to the FIRST occurrence that
            produced this artifact (``()`` for the root). Diagnostic only:
            every occurrence with the same definition, config and layout
            policy shares this artifact and this one transform call.
        pipeline_name: The graph's ``@pipeline`` display name (diagnostic).
        task_interfaces: Read-only map of EVERY graph task ID (in task order)
            -> its :class:`TaskInterface`: exact for ``@task`` and
            ``subpipeline`` tasks, observed-arguments/outputs-only (``approximate``)
            for opaque ``ref`` / ``@registered`` tasks. Geometry input only:
            interface data is never written back.
        artifact_dir: Directory the artifact will be written to; relative
            ``file://`` refs in the body resolve from here. The artifact
            itself and its compiler-generated siblings are not written yet.
    """

    layout: Layout
    path: tuple[str, ...]
    pipeline_name: str
    task_interfaces: Mapping[str, TaskInterface]
    artifact_dir: Path

    @property
    def algorithm(self) -> str | None:
        """Shortcut for ``layout.algorithm``."""
        return self.layout.algorithm


class GraphLayoutTransform(Protocol):
    """Compile-time layout callback passed to ``compile_pipeline``.

    Called once per covered artifact, with a private deep copy of the
    dehydrated pipeline body, after its refs are final and its subgraphs are
    compiled, and before validation and writing. It returns the body to
    write, which may differ only in ``editor.position`` annotations on graph
    tasks and on top-level ``inputs`` / ``outputs``. It must be deterministic:
    every occurrence sharing the artifact reuses the result.
    """

    def __call__(self, graph: dict[str, Any], context: GraphLayoutContext) -> dict[str, Any]: ...
