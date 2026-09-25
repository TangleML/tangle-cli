"""``graph_input()`` / ``graph_output()`` — declare graph I/O from the body.

A pipeline's inputs normally come from its ``In[T]`` parameters and its
outputs from the return annotation. That covers Python-shaped graphs only:
it cannot express a name that is not an identifier (``"Pipeline Creation
Time"``), an exact Tangle type string, an input declared conditionally, or
a per-input ``editor.position``. These two functions declare the same
entries directly on the active trace, so a YAML pipeline that uses those
shapes stays expressible in Python.

Entry key order follows the tracer's signature path and the corpus:
``name, type, description, default, optional, annotations``.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from tangle_cli.editor_layout import POSITION_ANNOTATION, position_annotation_value
from tangle_cli.schema_validation import CALLER_ANNOTATION_POLICY, check_annotations

from .errors import InvalidEditorLayoutError, InvalidGraphIoError
from .graph import EdgeRef
from .placeholders import GraphInputPlaceholder, TaskOutputProxy

# Distinguishes "no default" from an explicit ``default=None``, which is a
# value the schema does not accept and must be reported, not dropped.
_UNSET: Any = object()

__all__ = ["graph_input", "graph_output"]


def graph_input(
    name: str,
    type: str | None = None,
    *,
    description: str | None = None,
    default: Any = _UNSET,
    optional: bool | None = None,
    annotations: Mapping[str, str] | None = None,
    position: tuple[float, float] | None = None,
    when: bool = True,
) -> GraphInputPlaceholder | None:
    """Declare a graph input on the active pipeline and return its handle.

    The handle is passed to task arguments exactly like an ``In[T]``
    parameter. Supplying ``default`` implies ``optional: true`` unless
    ``optional=False`` is passed explicitly.

    ``when=False`` declares nothing and returns ``None``, so a conditional
    input reads as one assignment instead of an ``if``/``else``.

    Raises:
        InvalidGraphIoError: Outside a ``@pipeline`` body, on a duplicate
            name, or on an unusable field. Messages name the field, never
            the value.
    """
    builder = _active_builder("graph_input")
    if not when:
        return None

    _check_name(name, "graph_input")
    if any(entry.get("name") == name for entry in builder.inputs):
        # Also catches an ``In[...]`` parameter: signature inputs are
        # appended before the body runs.
        raise InvalidGraphIoError(
            f"graph input {name!r} is already declared on pipeline "
            f"{builder.name!r}. Input names must be unique."
        )

    entry: dict[str, Any] = {"name": name}
    if type is not None:
        entry["type"] = _check_str("type", type)
    if description is not None:
        entry["description"] = _check_str("description", description)
    if default is not _UNSET:
        # InputSpec.default is a string in the schema; a non-string default
        # would only fail later, against a schema path instead of this call.
        entry["default"] = _check_str("default", default, hint="pass str(value)")
        entry["optional"] = True if optional is None else _check_bool(optional)
    elif optional is not None:
        entry["optional"] = _check_bool(optional)
    merged = _layout_annotations(annotations, position)
    if merged:
        entry["annotations"] = merged

    builder.inputs.append(entry)
    return GraphInputPlaceholder(input_name=name)


def graph_output(
    name: str,
    value: Any,
    type: str | None = None,
    *,
    description: str | None = None,
    annotations: Mapping[str, str] | None = None,
    position: tuple[float, float] | None = None,
    when: bool = True,
) -> None:
    """Declare a graph output on the active pipeline, wired to ``value``.

    ``value`` is a task output handle or a graph-input handle; a constant is
    refused, matching the ``Outputs`` return path.

    Raises:
        InvalidGraphIoError: Outside a ``@pipeline`` body, on a duplicate
            name, on an unusable field, or on a constant value.
    """
    builder = _active_builder("graph_output")
    if not when:
        return

    _check_name(name, "graph_output")
    if any(entry.get("name") == name for entry in builder.outputs):
        raise InvalidGraphIoError(
            f"graph output {name!r} is already declared on pipeline "
            f"{builder.name!r}. Output names must be unique."
        )

    entry: dict[str, Any] = {"name": name}
    if type is not None:
        entry["type"] = _check_str("type", type)
    if description is not None:
        entry["description"] = _check_str("description", description)
    merged = _layout_annotations(annotations, position)
    if merged:
        entry["annotations"] = merged

    builder.outputs.append(entry)
    builder.output_values[name] = _edge(name, value)


def _active_builder(caller: str) -> Any:
    from .trace import current_builder

    builder = current_builder()
    if builder is None:
        raise InvalidGraphIoError(
            f"{caller}() must be called inside a @pipeline function body, "
            "where a trace is active."
        )
    return builder


def _edge(name: str, value: Any) -> EdgeRef:
    if isinstance(value, TaskOutputProxy):
        return EdgeRef(
            kind="taskOutput",
            task_id=value._task_id,
            output=value._resolved_output_name(),
        )
    if isinstance(value, GraphInputPlaceholder):
        return EdgeRef(kind="graphInput", input_name=value.input_name)
    raise InvalidGraphIoError(
        f"graph output {name!r} must be wired to a task output or a graph "
        f"input; got {type(value).__name__}. Constant graph outputs are not "
        "supported — emit the value from a task."
    )


def _layout_annotations(
    annotations: Mapping[str, str] | None,
    position: tuple[float, float] | None,
) -> dict[str, str]:
    merged: dict[str, str] = {}
    if annotations is not None:
        if not isinstance(annotations, Mapping):
            raise InvalidGraphIoError(
                f"annotations must be a mapping; got {type(annotations).__name__}."
            )
        check_annotations(
            annotations,
            policy=CALLER_ANNOTATION_POLICY,
            error_cls=InvalidGraphIoError,
        )
        merged.update(annotations)
    if position is not None:
        # Applied after the mapping, matching ``@pipeline(flow_direction=...)``.
        if not isinstance(position, tuple) or len(position) != 2:
            raise InvalidEditorLayoutError(
                "position must be an (x, y) tuple; got "
                f"{type(position).__name__}."
            )
        merged[POSITION_ANNOTATION] = position_annotation_value(
            *position, error_cls=InvalidEditorLayoutError
        )
    return merged


def _check_name(name: Any, caller: str) -> str:
    if not isinstance(name, str) or not name:
        raise InvalidGraphIoError(
            f"{caller}() name must be a non-empty string; got "
            f"{type(name).__name__}."
        )
    return name


def _check_str(field: str, value: Any, *, hint: str | None = None) -> str:
    if not isinstance(value, str):
        suffix = f". {hint.capitalize()}." if hint else "."
        raise InvalidGraphIoError(
            f"{field} must be a string; got {type(value).__name__}{suffix}"
        )
    return value


def _check_bool(value: Any) -> bool:
    if not isinstance(value, bool):
        raise InvalidGraphIoError(
            f"optional must be True or False; got {type(value).__name__}."
        )
    return value
