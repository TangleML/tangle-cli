"""IR → dict in canonical key order (the *dehydrated* pipeline shape).

Canonical top-level key order:
    name, description, metadata, inputs, outputs, implementation

Per-task key order:
    annotations?, componentRef, arguments

Argument values are emitted in the runnable ``ArgumentValue`` shape,
dispatched purely on the VALUE's runtime type — never on the argument
*key*. The compiler is operation-agnostic: ``wait_for``, ``project``,
``sql_query`` and ``payload`` are all treated identically. Only the
value decides the shape:

* :class:`TaskOutputProxy`       → ``{taskOutput: {taskId, outputName}}``
* :class:`GraphInputPlaceholder` → ``{graphInput: {inputName}}``
* a ``str`` constant             → the RAW string (no wrapper)
* a :class:`Raw` constant        → its inner string verbatim (no wrapper),
  exactly like a plain ``str`` constant — but the argument's JSON path is
  also recorded in the returned exempt-paths set so the no-template-delimiter
  output guard skips that one location (it is a legitimate RUNTIME
  placeholder, e.g. a run-query ``{{input_1}}`` sentinel).

Non-string constants are rejected: the runnable Tangle argument contract
only supports string constants, ``graphInput``, or ``taskOutput``.
Structured/non-string values must be stringified explicitly in pipeline
code (e.g. ``json.dumps(...)``) before they reach the compiler.

The literal key the user wrote (``wait_for``, ``depends_on``,
``project``, ``payload``, ...) is preserved verbatim as the dict key.
"""
from __future__ import annotations

from typing import Any

from .errors import CompileError, InvalidArgumentTypeError
from .graph import EdgeRef, GraphBuilder, TaskNode
from .placeholders import GraphInputPlaceholder, TaskOutputProxy
from .raw import Raw


def emit_pipeline(g: GraphBuilder) -> tuple[dict[str, Any], set[str]]:
    """Build the body dict for the compiled (dehydrated) pipeline YAML.

    Only keys with content are emitted (mirrors the source template:
    ``description`` and ``metadata`` are present iff the user set them;
    ``inputs`` and ``outputs`` are present iff non-empty).

    Returns:
        A ``(body_dict, exempt_paths)`` tuple. ``exempt_paths`` is the set
        of dot-delimited JSON paths (in the exact form the compiler's
        template-delimiter scan yields) for every argument whose value was
        wrapped in
        :func:`tangle_cli.python_pipeline.raw` — i.e. legitimate RUNTIME
        template placeholders the no-template-delimiter output guard must
        skip. Empty when no ``raw(...)`` value was used (the common case),
        so it is inert for callers that ignore it.

    Raises:
        CompileError: if the graph has no tasks (the schema requires at
            least one) or two tasks share the same id (the tasks dict
            would otherwise silently drop the earlier task).
    """
    # Collected as tasks are emitted; a Raw argument records its path in
    # the exact dot-delimited form ``iter_template_delimiters`` produces so
    # the guard can match and skip it.
    exempt_paths: set[str] = set()

    out: dict[str, Any] = {"name": g.name}

    if g.description:
        out["description"] = g.description

    if g.annotations:
        # metadata.annotations preserves user-specified order.
        out["metadata"] = {"annotations": dict(g.annotations)}

    if g.inputs:
        out["inputs"] = list(g.inputs)

    if g.outputs:
        out["outputs"] = list(g.outputs)

    graph: dict[str, Any] = {}

    # outputValues block is emitted BEFORE tasks within graph. Prefer the
    # MULTI-output map (Decision D) when present — it preserves Outputs field
    # declaration order — and fall back to the single-output compatibility
    # shims for the legacy ``-> Out[T]`` path.
    if g.output_values:
        graph["outputValues"] = {
            name: _emit_edge_value(edge) for name, edge in g.output_values.items()
        }
    elif g.output_name and g.output_taskref is not None:
        graph["outputValues"] = {
            g.output_name: _emit_edge_value(g.output_taskref),
        }

    # Reject an empty graph up front with a friendly message — the
    # schema also enforces this (minProperties: 1) but a raw schema error
    # is harder to act on.
    if not g.tasks:
        raise CompileError(
            "pipeline has no tasks; a compiled pipeline must declare at "
            "least one task. Call at least one ref(...)(...) inside the "
            "@pipeline body."
        )

    # Walk tasks in insertion order (= trace-time call order). Building
    # the dict directly would silently overwrite an earlier task that
    # shares an id, so detect duplicates explicitly.
    tasks_dict: dict[str, dict[str, Any]] = {}
    for node in g.tasks:
        if node.task_id in tasks_dict:
            raise CompileError(
                f"duplicate task id {node.task_id!r}: two tasks resolved to "
                "the same id. Give each task a unique id via "
                ".named('Unique Id') or a distinct assignment variable name."
            )
        # The task's argument JSON paths are rooted at
        # ``implementation.graph.tasks.<task_id>`` — the SAME prefix
        # ``iter_template_delimiters`` walks to — so any Raw argument is
        # recorded under a path the output guard can match verbatim.
        task_path = f"implementation.graph.tasks.{node.task_id}"
        tasks_dict[node.task_id] = _emit_task(node, task_path, exempt_paths)
    graph["tasks"] = tasks_dict

    out["implementation"] = {"graph": graph}
    return out, exempt_paths


# ---------------------------------------------------------------------------
# Internal helpers


def _emit_task(
    node: TaskNode, task_path: str, exempt_paths: set[str]
) -> dict[str, Any]:
    """Build the per-task body dict in canonical key order:
    ``annotations?, componentRef, arguments``.

    ``task_path`` is this task's dot-delimited JSON path
    (``implementation.graph.tasks.<task_id>``); each argument's path is
    derived from it so a :class:`Raw` value can record an exempt path that
    the output guard matches verbatim. ``exempt_paths`` is mutated in place.
    """
    body: dict[str, Any] = {}

    if node.annotations:
        # Preserve annotation values as authored — Tangle annotations are
        # string-valued in practice but the schema allows free-form.
        body["annotations"] = {k: v for k, v in node.annotations.items()}

    body["componentRef"] = _emit_component_ref(node)

    if node.arguments:
        args_path = f"{task_path}.arguments"
        body["arguments"] = {
            k: _emit_argument_value(k, v, f"{args_path}.{k}", exempt_paths)
            for k, v in node.arguments.items()
        }

    return body


def _emit_component_ref(node: TaskNode) -> dict[str, Any]:
    """Render ``componentRef`` as a PURE ref — ``{url[, digest]}``,
    ``{name[, digest]}`` or ``{digest}``. Never emits ``spec`` or ``text``.

    Locator dispatch (first matching branch wins):

    * ``ref_url`` set        -> ``{"url": …}``; a ``ref_digest`` pins it
      (``{"url": …, "digest": …}``). Subpipeline refs always take this
      branch via their ``subpipeline://pending`` sentinel URL.
    * ``ref_name`` set       -> ``{"name": …}``; a ``ref_digest`` pins it
      (``{"name": …, "digest": …}``).
    * ``ref_digest`` alone   -> ``{"digest": …}``.

    A node with ``ref_url=None`` AND no name/digest is only reachable via
    ``@task`` refs (the ``ref()`` factory rejects a no-locator call). Such
    a node gets a transient ``local-from-python://pending`` placeholder
    URL here; the compile driver REWRITES it to a real
    ``resolve://./<stem>.components.yaml#<fragment>`` URL (via
    ``_rewrite_task_componentref_urls``) BEFORE schema validation, so the
    placeholder never reaches the written output. It is still a pure ref
    — no ``spec``/``text``.
    """
    if node.ref_url:
        cref: dict[str, Any] = {"url": node.ref_url}
        if node.ref_digest:
            cref["digest"] = node.ref_digest
        return cref
    if node.ref_name:
        cref = {"name": node.ref_name}
        if node.ref_digest:
            cref["digest"] = node.ref_digest
        return cref
    if node.ref_digest:
        return {"digest": node.ref_digest}
    # Transient placeholder for @task refs. The compile driver computes
    # the real ``resolve://./<stem>.components.yaml#<fragment>`` URL after
    # tracing (it depends on the output path) and rewrites this in place
    # before validation. Still a pure ref — no spec/text.
    return {"url": _TASK_URL_PLACEHOLDER}


# Sentinel URL written by ``_emit_component_ref`` for @task refs whose
# real URL (``resolve://./<stem>.components.yaml#<fragment>``) is only
# known after the driver has computed output paths. The driver always
# rewrites it before writing, so it never appears in compiled output.
_TASK_URL_PLACEHOLDER = "local-from-python://pending"


def _emit_argument_value(
    key: str, value: Any, arg_path: str, exempt_paths: set[str]
) -> Any:
    """Render an argument value in its runnable ``ArgumentValue`` form.

    Dispatch is purely on the VALUE's runtime type — the argument *key*
    is never inspected. Produces a ``taskOutput`` / ``graphInput`` wrapper
    for edges, or the RAW string for a constant (matching the runnable
    Tangle argument contract). Non-string constants are rejected.

    A :class:`Raw` value is emitted as its inner string verbatim — exactly
    like a plain ``str`` constant — and ``arg_path`` (this argument's
    JSON path) is added to ``exempt_paths`` so the no-template-delimiter
    output guard skips this one location: the inner string is a legitimate
    RUNTIME placeholder (e.g. a run-query ``{{input_1}}`` sentinel), not a
    leaked compile-time template.
    """
    if isinstance(value, TaskOutputProxy):
        return {
            "taskOutput": {
                "taskId": value._task_id,
                "outputName": value._resolved_output_name(),
            }
        }
    if isinstance(value, GraphInputPlaceholder):
        return {"graphInput": {"inputName": value.input_name}}
    if isinstance(value, Raw):
        # Emit the inner string verbatim (identical to a str constant) and
        # exempt this argument's path from the delimiter guard. Rawness is
        # lost once the value is a plain str in the dict, so it must be
        # recorded here, at the only point the Raw wrapper is still visible.
        exempt_paths.add(arg_path)
        return value.value
    # Everything else is a constant. The runnable schema only accepts raw
    # string constants, so validate and emit the string verbatim.
    _validate_constant(value, key)
    return value


def _validate_constant(value: Any, key: str) -> None:
    """Assert ``value`` is a runnable string constant.

    Runnable Tangle pipeline arguments only support raw ``str`` constants
    (alongside the ``graphInput`` / ``taskOutput`` wrappers). A non-string
    constant (``int``, ``float``, ``bool``, ``None``, ``list``, ``dict``,
    a tuple/set, a leftover helper object, a callable, ...) cannot be
    represented under the runnable schema, so it is rejected with a
    GENERIC, operation-agnostic message — the compiler has no
    operation-specific knowledge (no SQL, BigQuery, or other domain
    awareness). Authors must stringify structured/non-string values
    explicitly in pipeline code before passing them as task arguments.
    """
    if isinstance(value, str):
        return
    raise InvalidArgumentTypeError(
        f"unsupported constant type {type(value).__name__!r} for "
        f"argument {key!r}. Runnable Tangle pipeline arguments only support "
        "string constants, graphInput, or taskOutput. Convert structured or "
        "non-string values to a string explicitly in your pipeline code "
        "(for example json.dumps(...) or str(...)) before passing them as "
        "task arguments."
    )


def _emit_edge_value(edge: EdgeRef) -> dict[str, Any]:
    """Render an :class:`EdgeRef` as a dehydrated ``ArgumentValue``
    sub-dict (``{taskOutput|graphInput: {...}}``) used in
    ``outputValues``."""
    if edge.kind == "taskOutput":
        return {
            "taskOutput": {
                "taskId": edge.task_id,
                "outputName": edge.output or "wait_for_output",
            }
        }
    return {"graphInput": {"inputName": edge.input_name}}
