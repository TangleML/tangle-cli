"""The tracer: runs a ``@pipeline`` body inside a fresh GraphBuilder.

Public API:

* :func:`current_builder` — returns the currently active GraphBuilder
  (or ``None`` outside a trace). ``CallableRef.__call__`` consults this
  to attach new tasks.

* :func:`trace_pipeline` — the compile driver entry point. Builds a
  fresh builder, binds ``cfg`` and ``In[T]`` parameters, sets up the
  AST pre-pass for LHS-name auto-IDs, calls the user's pipeline
  function, captures the ``Out[T]`` return slot, and returns the
  populated builder.

Trace mechanics for task IDs:

* At ``trace_pipeline`` start we ``inspect.getsource(fn)`` and build a
  ``{absolute_lineno: lhs_name}`` map by walking ``ast.Assign`` nodes
  whose target is a single ``Name``.
* At trace time, ``CallableRef.__call__`` reads
  ``sys._getframe(1).f_lineno`` and consults the map. If the line has
  no clean Name-LHS, the call must have been wrapped in ``.named(...)``
  or we raise :class:`AmbiguousTaskIdError`.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import textwrap
import typing
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from .errors import CompileError
from .graph import EdgeRef, GraphBuilder
from .placeholders import GraphInputPlaceholder, TaskOutputProxy
from .types import In, Out, Outputs

if TYPE_CHECKING:  # pragma: no cover
    from .pipeline import PipelineFn


_BUILDER: ContextVar["GraphBuilder | None"] = ContextVar("_BUILDER", default=None)


def current_builder() -> "GraphBuilder | None":
    """Return the active trace builder, or ``None`` outside a trace."""
    return _BUILDER.get()


def _build_lineno_to_lhs(fn: Any) -> dict[int, str]:
    """Walk ``fn``'s source and map each absolute line number containing
    a single-Name assignment to that name.

    Returns ``{}`` for pipelines defined in places where source isn't
    available (e.g. exec'd strings without ``inspect`` support); callers
    must always handle the ``.get(lineno) -> None`` case.
    """
    try:
        src_lines, start_lineno = inspect.getsourcelines(fn)
    except (OSError, TypeError):
        return {}
    src = textwrap.dedent("".join(src_lines))
    try:
        tree = ast.parse(src)
    except SyntaxError:  # pragma: no cover — defensive
        return {}

    out: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name):
                # node.lineno is 1-based in the dedented source. We
                # want absolute file line numbers to match
                # sys._getframe.f_lineno in the trace, which reports the
                # line of the inner call (often the LAST line of the
                # RHS for multi-line expressions). Map EVERY line
                # spanned by the assignment to the LHS name so the call
                # can be on any of them.
                end_lineno = getattr(node, "end_lineno", node.lineno) or node.lineno
                for ln in range(node.lineno, end_lineno + 1):
                    out[ln + start_lineno - 1] = target.id
    return out


def _is_in_annotation(annotation: Any) -> bool:
    """True when ``annotation`` is ``In[T]`` (PEP 484/585 generic alias)."""
    return getattr(annotation, "__origin__", None) is In


def _is_out_annotation(annotation: Any) -> bool:
    return getattr(annotation, "__origin__", None) is Out


def _is_outputs_class(annotation: Any) -> bool:
    """True when ``annotation`` is a STRICT subclass of :class:`Outputs`
    (the multi-output typed-object marker). The bare ``Outputs`` base is
    not itself a valid return annotation."""
    return (
        isinstance(annotation, type)
        and issubclass(annotation, Outputs)
        and annotation is not Outputs
    )


def _outputs_fields(outputs_cls: type) -> dict[str, Any]:
    """Return ordered ``{field_name: Out[T] inner type | None}`` for an
    :class:`Outputs` subclass.

    Field order follows dataclass declaration order (or ``__annotations__``
    order for a non-dataclass). The inner type is the ``T`` of an ``Out[T]``
    field annotation, or ``None`` when the field is not annotated ``Out[T]``
    (the caller validates and rejects that case at trace time).
    """
    try:
        hints = typing.get_type_hints(outputs_cls, include_extras=True)
    except Exception:
        hints = dict(getattr(outputs_cls, "__annotations__", {}))
    if dataclasses.is_dataclass(outputs_cls):
        names = [f.name for f in dataclasses.fields(outputs_cls)]
    else:
        names = list(getattr(outputs_cls, "__annotations__", {}).keys())
    result: dict[str, Any] = {}
    for name in names:
        anno = hints.get(name)
        result[name] = _annotation_inner_type(anno) if _is_out_annotation(anno) else None
    return result


def _annotation_inner_type(annotation: Any) -> Any:
    """Extract ``T`` from ``In[T]`` / ``Out[T]``."""
    args = getattr(annotation, "__args__", ())
    return args[0] if args else None


def declared_output_names(pipeline_fn: "PipelineFn") -> tuple[str, ...] | None:
    """Best-effort static list of a child pipeline's declared output names.

    Derived from the child ``PipelineFn``'s return annotation at PARENT
    trace time so ``subpipeline(child)(...)`` can return a strict
    :class:`SubpipelineOutputProxy` BEFORE the child is compiled:

    * single ``-> Out[T]`` -> ``(pipeline_fn.output_name,)``;
    * an ``Outputs`` subclass -> its ``Out[T]`` field names, in field order;
    * anything else / unresolvable -> ``None`` (permissive fallback).

    The compiled child sidecar's ``outputs`` block remains the authoritative
    source for the compiler's cross-file validation; this is the trace-time
    view used purely for proxy ergonomics.
    """
    try:
        hints = typing.get_type_hints(pipeline_fn.fn, include_extras=True)
        return_anno = hints.get(
            "return", inspect.signature(pipeline_fn.fn).return_annotation
        )
    except Exception:
        return None
    if return_anno is inspect.Signature.empty:
        return None
    if _is_out_annotation(return_anno):
        return (pipeline_fn.output_name,)
    if _is_outputs_class(return_anno):
        return tuple(_outputs_fields(return_anno).keys())
    return None


def _python_type_to_tangle_type(t: Any) -> str:
    """Map ``str``/``int``/etc. to Tangle type strings."""
    if t is str:
        return "String"
    if t is int:
        return "Integer"
    if t is float:
        return "Float"
    if t is bool:
        return "Boolean"
    # Fallback: stringify whatever it is. Tangle tolerates arbitrary
    # type strings so long as the consumer recognises them.
    return getattr(t, "__name__", str(t))


def trace_pipeline(
    pipeline_fn: "PipelineFn",
    cfg: Any,
    inputs: dict[str, Any] | None = None,
) -> GraphBuilder:
    """Trace ``pipeline_fn`` against ``cfg`` (+ optional runtime ``inputs``).

    Returns the populated :class:`GraphBuilder` ready for emit.

    The function's parameters are inspected:
    - ``cfg`` (no ``In`` annotation, named exactly ``cfg``) → bound to
      the loaded :class:`Cfg` object.
    - ``In[T]`` parameters → :class:`GraphInputPlaceholder` (no default)
      or use the user-provided ``inputs`` dict / the parameter default.
      Each becomes an entry in ``builder.inputs`` with type info.
    - Any other parameter → :class:`CompileError`.

    The return annotation is inspected:
    - ``Out[T]`` → ``builder.outputs`` gets a single entry, and the
      return value (which must be a :class:`TaskOutputProxy`) is wired
      into ``builder.output_taskref`` via an :class:`EdgeRef`.
    - No annotation / ``None`` → no outputs block.
    """
    inputs = inputs or {}

    builder = GraphBuilder(
        name=pipeline_fn.name,
        description=pipeline_fn.description,
        annotations=dict(pipeline_fn.annotations),
    )
    # AST pre-pass: needed by CallableRef.__call__ to derive task IDs
    # from LHS variable names. Stashed on the builder so the contextvar
    # carries everything in one bag.
    builder.lineno_to_lhs = _build_lineno_to_lhs(pipeline_fn.fn)

    sig = inspect.signature(pipeline_fn.fn)
    # Resolve PEP 563 string annotations to runtime values. This must
    # use the user fn's own globals so In/Out/etc. resolve correctly
    # when the user has ``from __future__ import annotations``.
    try:
        resolved_hints = typing.get_type_hints(pipeline_fn.fn, include_extras=True)
    except Exception:
        # Best effort: if name resolution fails (e.g. forward refs that
        # can't be resolved at trace time), fall back to the raw
        # annotation strings on each parameter.
        resolved_hints = {}
    call_kwargs: dict[str, Any] = {}

    for param_name, param in sig.parameters.items():
        annotation = resolved_hints.get(param_name, param.annotation)

        if param_name == "cfg" and not _is_in_annotation(annotation):
            call_kwargs[param_name] = cfg
            continue

        if _is_in_annotation(annotation):
            inner = _annotation_inner_type(annotation)
            type_str = _python_type_to_tangle_type(inner)
            entry: dict[str, Any] = {"name": param_name, "type": type_str}

            has_default = param.default is not inspect.Parameter.empty
            if has_default:
                entry["default"] = param.default
                entry["optional"] = True
            builder.inputs.append(entry)

            # Bind a placeholder for the user fn body. If the caller
            # supplied a runtime input, prefer that; otherwise the
            # placeholder carries the input name so emit can render it.
            if param_name in inputs:
                call_kwargs[param_name] = inputs[param_name]
            else:
                call_kwargs[param_name] = GraphInputPlaceholder(input_name=param_name)
            continue

        raise CompileError(
            f"@pipeline parameter {param_name!r} is not annotated In[T] and is not "
            f"named 'cfg'. Use In[T] for graph inputs, or name the parameter 'cfg' "
            "for the loaded config object."
        )

    # Run the user's pipeline body inside a context where
    # current_builder() returns this builder.
    token = _BUILDER.set(builder)
    try:
        result = pipeline_fn.fn(**call_kwargs)
    finally:
        _BUILDER.reset(token)

    # Capture the declared output(s). ``get_type_hints`` returns the
    # resolved return annotation under the ``return`` key. Two shapes:
    #   * single ``-> Out[T]``     -> one output (legacy path, unchanged);
    #   * ``-> <Outputs subclass>`` -> one output per Out[T] field.
    return_anno = resolved_hints.get("return", sig.return_annotation)
    if return_anno is not inspect.Signature.empty and _is_out_annotation(return_anno):
        if not isinstance(result, TaskOutputProxy):
            raise CompileError(
                "@pipeline declared -> Out[T] but the function did not return a "
                "TaskOutputProxy. Return the result of the final task call (e.g. "
                "`return publish_to_comet`)."
            )
        inner = _annotation_inner_type(return_anno)
        type_str = _python_type_to_tangle_type(inner)
        output_name = pipeline_fn.output_name
        builder.outputs.append({"name": output_name, "type": type_str})
        builder.output_name = output_name
        builder.output_taskref = EdgeRef(
            kind="taskOutput",
            task_id=result._task_id,
            output=result._resolved_output_name(),
        )
    elif return_anno is not inspect.Signature.empty and _is_outputs_class(return_anno):
        _trace_multi_output(builder, pipeline_fn, return_anno, result)

    return builder


def _trace_multi_output(
    builder: GraphBuilder,
    pipeline_fn: "PipelineFn",
    outputs_cls: type,
    result: Any,
) -> None:
    """Populate ``builder`` for a pipeline returning an :class:`Outputs`
    typed object.

    Emits one ``builder.outputs`` entry and one ``builder.output_values``
    edge per ``Out[T]`` field, in field declaration order (deterministic).
    Each field value must be a :class:`TaskOutputProxy` or
    :class:`GraphInputPlaceholder` (v1 forbids constant graph outputs).
    """
    # ``@pipeline(output_name=...)`` applies only to the single-output path.
    if pipeline_fn.output_name != "wait_for_output":
        raise CompileError(
            "@pipeline(output_name=...) cannot be combined with an Outputs "
            f"return annotation ({outputs_cls.__name__}); output names come "
            f"from the Outputs fields. Remove output_name="
            f"{pipeline_fn.output_name!r}."
        )
    if not isinstance(result, outputs_cls):
        raise CompileError(
            f"@pipeline declared -> {outputs_cls.__name__} but the function "
            f"returned a {type(result).__name__}. Return an instance, e.g. "
            f"`return {outputs_cls.__name__}(...)`."
        )
    fields = _outputs_fields(outputs_cls)
    if not fields:
        raise CompileError(
            f"Outputs subclass {outputs_cls.__name__} declares no fields; add "
            "at least one Out[T]-annotated field."
        )
    for field_name, inner in fields.items():
        if inner is None:
            raise CompileError(
                f"Outputs field {field_name!r} on {outputs_cls.__name__} must "
                "be annotated Out[T] (e.g. `rows_written: Out[str]`)."
            )
        value = getattr(result, field_name)
        if isinstance(value, TaskOutputProxy):
            edge = EdgeRef(
                kind="taskOutput",
                task_id=value._task_id,
                output=value._resolved_output_name(),
            )
        elif isinstance(value, GraphInputPlaceholder):
            edge = EdgeRef(kind="graphInput", input_name=value.input_name)
        else:
            raise CompileError(
                f"Outputs field {field_name!r} must be wired to a task output "
                "or a graph input (a TaskOutputProxy or In[...] value), got "
                f"{type(value).__name__}. v1 does not support constant graph "
                "outputs."
            )
        type_str = _python_type_to_tangle_type(inner)
        builder.outputs.append({"name": field_name, "type": type_str})
        builder.output_values[field_name] = edge


# Re-export commonly-used names.
__all__ = ["current_builder", "trace_pipeline"]
