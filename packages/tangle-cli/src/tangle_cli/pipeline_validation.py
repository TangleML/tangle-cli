"""Pipeline authoring validation for Tangle pipeline specs.

Validation covers:
- root and graph shape checks used by local authoring commands;
- the vendored Tangle JSON schema;
- component input wiring when component specs are available.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any, Iterable, Mapping

import yaml

from .pipeline_spec_utils import _extract_task_output_refs

PIPELINE_GRAPH_PATH = "implementation.graph"
TASKS_PATH = f"{PIPELINE_GRAPH_PATH}.tasks"

__all__ = [
    "PipelineValidationError",
    "collect_pipeline_spec_errors",
    "load_pipeline_schema",
    "validate_component_inputs",
    "validate_pipeline_schema",
    "validate_pipeline_spec",
]


class PipelineValidationError(ValueError):
    """Raised when a local pipeline spec cannot be parsed or validated."""


def collect_pipeline_spec_errors(pipeline: Mapping[str, Any]) -> list[str]:
    """Return all OSS-compatible local pipeline authoring errors.

    Runs graph-shape checks, the packaged Tangle pipeline JSON schema, and
    component input wiring validation. The returned strings are suitable for
    CLI display; no exception is raised by this collector.
    """

    errors: list[str] = []
    _validate_root_pipeline(pipeline, errors)
    errors.extend(validate_pipeline_schema(pipeline))
    errors.extend(validate_component_inputs(pipeline))
    return errors


def validate_pipeline_spec(pipeline: Mapping[str, Any]) -> None:
    """Raise PipelineValidationError when a pipeline spec has local errors."""

    errors = collect_pipeline_spec_errors(pipeline)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise PipelineValidationError(f"Pipeline validation failed:\n{details}")


@lru_cache(maxsize=1)
def load_pipeline_schema() -> dict[str, Any]:
    """Load and cache the packaged Tangle pipeline JSON schema object."""

    schema_text = resources.files("tangle_cli.schemas").joinpath("pipeline_schema.json").read_text()
    schema = json.loads(schema_text)
    if not isinstance(schema, dict):
        raise PipelineValidationError("Vendored pipeline schema must be a JSON object")
    return schema


def _find_deepest_type_error(error: Any) -> Any | None:
    """Return the deepest nested jsonschema type error, if one exists."""
    best_error: Any | None = None
    best_depth = -1

    def search(err: Any, depth: int = 0) -> None:
        """Walk nested schema errors and update the deepest type-error match."""
        nonlocal best_error, best_depth
        if err.validator == "type" and depth > best_depth:
            best_error = err
            best_depth = depth
        for suberror in err.context or []:
            search(suberror, depth + 1)

    search(error)
    return best_error


def _truncate(value: str, max_len: int = 50) -> str:
    """Return value shortened to max_len characters, using an ellipsis."""
    return value[:max_len] + "..." if len(value) > max_len else value


def _format_type_error(path: str, actual_type: str, expected_type: str, instance: Any) -> str:
    """Return a readable type-mismatch message for a schema path."""
    actual_value = _truncate(repr(instance))
    return f"'{path}' must be {expected_type}, got {actual_type} ({actual_value})"


def _format_schema_error(error: Any, *, verbose: bool = False) -> str:
    """Return a CLI-friendly message for one jsonschema validation error."""
    path = ".".join(str(p) for p in error.absolute_path) if error.absolute_path else "root"

    if error.validator == "type":
        actual_type = type(error.instance).__name__
        if actual_type == "list" and error.validator_value == "object":
            return f"'{path}' must be an object (dict), not a list. Remove the '-' prefix before keys."
        return _format_type_error(path, actual_type, str(error.validator_value), error.instance)

    if error.validator == "required":
        return f"'{path}' is missing required field(s): {error.validator_value}"

    if error.validator == "anyOf":
        deepest = _find_deepest_type_error(error)
        if deepest:
            nested_path = ".".join(str(p) for p in deepest.absolute_path)
            return _format_type_error(
                nested_path,
                type(deepest.instance).__name__,
                str(deepest.validator_value),
                deepest.instance,
            )
        actual_value = _truncate(repr(error.instance))
        return f"'{path}' doesn't match any valid format (got {type(error.instance).__name__}: {actual_value})"

    msg = error.message if verbose or len(error.message) <= 200 else error.message[:200] + "..."
    return f"'{path}': {msg}"


def _should_ignore_schema_error(error: Any) -> bool:
    """Return True for schema constraints intentionally skipped in OSS CLI."""

    # The upstream generated schema currently narrows graph task argument values
    # to strings or reference objects, but OSS submit payloads support arbitrary
    # JSON/YAML literal values. Keep structural schema checks while preserving
    # that existing authoring behavior.
    return "arguments" in error.absolute_path


def validate_pipeline_schema(
    pipeline_spec: Mapping[str, Any],
    *,
    verbose: bool = False,
) -> list[str]:
    """Return JSON-schema validation errors for a pipeline spec.

    Schema errors under graph task ``arguments`` are ignored so existing OSS
    authoring can keep arbitrary literal JSON/YAML values.
    """

    import jsonschema

    validator = jsonschema.Draft7Validator(load_pipeline_schema())
    return [
        _format_schema_error(error, verbose=verbose)
        for error in validator.iter_errors(pipeline_spec)
        if not _should_ignore_schema_error(error)
    ]


def _get_component_spec(task: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return a task's embedded component spec, or None when unavailable.

    The spec may be supplied directly as ``componentRef.spec`` or as YAML text
    in ``componentRef.text``. Invalid YAML text is treated as unavailable.
    """

    component_ref = task.get("componentRef", {})
    if not isinstance(component_ref, Mapping):
        return None

    nested_spec = component_ref.get("spec")
    if isinstance(nested_spec, Mapping):
        return nested_spec

    text = component_ref.get("text")
    if isinstance(text, str):
        try:
            loaded = yaml.safe_load(text)
        except yaml.YAMLError:
            return None
        if isinstance(loaded, Mapping):
            return loaded

    return None


def _is_input_required(input_spec: Mapping[str, Any]) -> bool:
    """Return True when a component input is non-optional and lacks a default."""

    is_optional = input_spec.get("optional", False)
    has_default = "default" in input_spec
    return not is_optional and not has_default


def _get_task_outputs(task_spec: Mapping[str, Any]) -> set[str] | None:
    """Return declared task outputs, or None when the component spec is unknown."""

    component_spec = _get_component_spec(task_spec)
    if not component_spec:
        return None

    outputs = component_spec.get("outputs", [])
    if not isinstance(outputs, list):
        return set()
    return {str(out.get("name")) for out in outputs if isinstance(out, Mapping) and out.get("name")}


def _validate_graph_input_ref(
    arg_value: Any,
    graph_inputs: set[str],
    full_task_name: str,
    input_name: str,
) -> str | None:
    """Return an error if arg_value references a missing graph input.

    Non-``graphInput`` values and malformed references return None so other
    validators can handle their own structural checks.
    """

    if not isinstance(arg_value, Mapping) or "graphInput" not in arg_value:
        return None
    graph_input = arg_value.get("graphInput")
    if not isinstance(graph_input, Mapping):
        return None

    ref_input_name = graph_input.get("inputName")
    if ref_input_name and ref_input_name not in graph_inputs:
        return (
            f"Task '{full_task_name}': input '{input_name}' references "
            f"non-existent graph input '{ref_input_name}'"
        )
    return None


def _validate_task_output_ref(
    arg_value: Any,
    tasks: Mapping[str, Any],
    task_outputs: Mapping[str, set[str] | None],
    full_task_name: str,
    input_name: str,
) -> str | None:
    """Return an error if arg_value references a missing task or output.

    Non-``taskOutput`` values and malformed references return None so schema
    and graph-shape validation remain responsible for structural errors. Output
    names are checked only when the referenced task's component spec is known.
    """

    if not isinstance(arg_value, Mapping) or "taskOutput" not in arg_value:
        return None
    task_output_ref = arg_value.get("taskOutput")
    if not isinstance(task_output_ref, Mapping):
        return None

    ref_task_id = task_output_ref.get("taskId")
    ref_output_name = task_output_ref.get("outputName")

    if ref_task_id and ref_task_id not in tasks:
        return (
            f"Task '{full_task_name}': input '{input_name}' references "
            f"non-existent task '{ref_task_id}'"
        )

    if ref_task_id and ref_output_name:
        available_outputs = task_outputs.get(str(ref_task_id))
        if available_outputs is not None and ref_output_name not in available_outputs:
            return (
                f"Task '{full_task_name}': input '{input_name}' references "
                f"non-existent output '{ref_output_name}' on task '{ref_task_id}'"
            )
    return None


def _validate_task_inputs(
    task_name: str,
    task_spec: Mapping[str, Any],
    tasks: Mapping[str, Any],
    task_outputs: Mapping[str, set[str] | None],
    graph_inputs: set[str],
    path_prefix: str,
) -> list[str]:
    """Return component-input wiring errors for one task.

    Required component inputs must be present in task arguments. When declared
    inputs are explicitly supplied, graph/task output references are checked
    regardless of whether the input is required. Nested graph component specs
    are validated recursively.
    """

    errors: list[str] = []
    full_task_name = f"{path_prefix}{task_name}" if path_prefix else task_name
    component_spec = _get_component_spec(task_spec)
    if not component_spec:
        return errors

    component_inputs = component_spec.get("inputs", [])
    if not isinstance(component_inputs, list):
        component_inputs = []
    task_arguments = task_spec.get("arguments", {}) or {}
    if not isinstance(task_arguments, Mapping):
        task_arguments = {}

    for input_spec in component_inputs:
        if not isinstance(input_spec, Mapping):
            continue
        input_name = input_spec.get("name")
        if not input_name:
            continue
        input_name = str(input_name)

        if input_name not in task_arguments:
            if _is_input_required(input_spec):
                errors.append(
                    f"Task '{full_task_name}': required input '{input_name}' "
                    "has no value or connection"
                )
            continue

        arg_value = task_arguments[input_name]
        error = _validate_graph_input_ref(arg_value, graph_inputs, full_task_name, input_name)
        if error:
            errors.append(error)

        error = _validate_task_output_ref(arg_value, tasks, task_outputs, full_task_name, input_name)
        if error:
            errors.append(error)

    implementation = component_spec.get("implementation", {})
    nested_graph = implementation.get("graph") if isinstance(implementation, Mapping) else None
    if isinstance(nested_graph, Mapping):
        subgraph_inputs = {
            str(inp.get("name"))
            for inp in component_inputs
            if isinstance(inp, Mapping) and inp.get("name")
        }
        errors.extend(_validate_graph_inputs(nested_graph, subgraph_inputs, f"{full_task_name} > "))

    return errors


def _validate_graph_inputs(
    graph_spec: Mapping[str, Any],
    graph_inputs: set[str],
    path_prefix: str = "",
) -> list[str]:
    """Return component-input wiring errors for every task in a graph."""

    tasks = graph_spec.get("tasks", {})
    if not isinstance(tasks, Mapping) or not tasks:
        return []

    task_outputs = {
        str(name): _get_task_outputs(spec)
        for name, spec in tasks.items()
        if isinstance(spec, Mapping)
    }

    errors: list[str] = []
    for task_name, task_spec in tasks.items():
        if isinstance(task_name, str) and isinstance(task_spec, Mapping):
            errors.extend(
                _validate_task_inputs(
                    task_name,
                    task_spec,
                    tasks,
                    task_outputs,
                    graph_inputs,
                    path_prefix,
                )
            )

    return errors


def validate_component_inputs(pipeline_spec: Mapping[str, Any]) -> list[str]:
    """Return required-input and reference-wiring errors for a pipeline.

    Validation uses embedded component specs when present. If the pipeline has
    no object-shaped implementation graph, this component-input pass returns no
    errors and leaves graph-shape reporting to the root validator.
    """

    implementation = pipeline_spec.get("implementation", {})
    graph = implementation.get("graph") if isinstance(implementation, Mapping) else None
    if not isinstance(graph, Mapping):
        return []

    pipeline_inputs: set[str] = {
        str(inp.get("name"))
        for inp in pipeline_spec.get("inputs", [])
        if isinstance(inp, Mapping) and inp.get("name")
    }

    return _validate_graph_inputs(graph, pipeline_inputs)


def _validate_root_pipeline(pipeline: Mapping[str, Any], errors: list[str]) -> None:
    """Append root pipeline shape errors to the passed errors list.

    Requires a non-empty ``name`` and object-shaped ``implementation.graph``;
    when those are present, delegates recursive graph validation.
    """

    name = pipeline.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("name must be a non-empty string")

    implementation = pipeline.get("implementation")
    if not isinstance(implementation, Mapping):
        errors.append("implementation must be an object")
        return

    graph = implementation.get("graph")
    if not isinstance(graph, Mapping):
        errors.append(f"{PIPELINE_GRAPH_PATH} must be an object")
        return

    _validate_graph_spec(pipeline, "pipeline", errors, require_tasks=True)


def _validate_graph_spec(
    spec: Mapping[str, Any],
    path: str,
    errors: list[str],
    *,
    require_tasks: bool,
) -> None:
    """Append graph-shape and dependency errors for spec to errors.

    ``path`` prefixes human-readable messages. When ``require_tasks`` is False,
    nested component specs without graph tasks are accepted.
    """

    implementation = spec.get("implementation")
    if not isinstance(implementation, Mapping):
        if require_tasks:
            errors.append(f"{path}.implementation must be an object")
        return

    graph = implementation.get("graph")
    if not isinstance(graph, Mapping):
        if require_tasks:
            errors.append(f"{path}.{PIPELINE_GRAPH_PATH} must be an object")
        return

    tasks = graph.get("tasks")
    if tasks is None and not require_tasks:
        return
    if not isinstance(tasks, Mapping):
        errors.append(f"{path}.{TASKS_PATH} must be an object")
        return

    task_names: set[str] = set()
    for name in tasks.keys():
        if not isinstance(name, str):
            errors.append(f"{path}.{TASKS_PATH} task ids must be strings")
            continue
        task_names.add(name)
    edges: set[tuple[str, str]] = set()

    for task_name, raw_task in tasks.items():
        task_path = f"{path}.{TASKS_PATH}.{task_name}"
        if not isinstance(task_name, str):
            continue
        if not isinstance(raw_task, Mapping):
            errors.append(f"{task_path} must be an object")
            continue

        component_ref = raw_task.get("componentRef")
        if not isinstance(component_ref, Mapping):
            errors.append(f"{task_path}.componentRef must be an object")
        else:
            _validate_component_ref(component_ref, f"{task_path}.componentRef", errors)

        dependencies = raw_task.get("dependencies", [])
        if dependencies is None:
            dependencies = []
        if not isinstance(dependencies, list):
            errors.append(f"{task_path}.dependencies must be a list of task ids")
        else:
            for dep in dependencies:
                if not isinstance(dep, str):
                    errors.append(f"{task_path}.dependencies entries must be strings")
                    continue
                if dep not in task_names:
                    errors.append(f"{task_path}.dependencies references unknown task {dep!r}")
                else:
                    edges.add((dep, str(task_name)))

        arguments = raw_task.get("arguments", {})
        if arguments is not None and not isinstance(arguments, Mapping):
            errors.append(f"{task_path}.arguments must be an object")
        else:
            for referenced_task in _extract_task_output_refs(arguments or {}):
                if referenced_task not in task_names:
                    errors.append(
                        f"{task_path}.arguments references unknown task {referenced_task!r}"
                    )
                else:
                    edges.add((referenced_task, str(task_name)))

        if isinstance(component_ref, Mapping):
            nested_spec = component_ref.get("spec")
            if isinstance(nested_spec, Mapping):
                _validate_graph_spec(
                    nested_spec,
                    f"{task_path}.componentRef.spec",
                    errors,
                    require_tasks=False,
                )

    output_values = graph.get("outputValues", {})
    if output_values is not None and not isinstance(output_values, Mapping):
        errors.append(f"{path}.{PIPELINE_GRAPH_PATH}.outputValues must be an object")
    else:
        for referenced_task in _extract_task_output_refs(output_values or {}):
            if referenced_task not in task_names:
                errors.append(
                    f"{path}.{PIPELINE_GRAPH_PATH}.outputValues references unknown task "
                    f"{referenced_task!r}"
                )

    cycle = _find_cycle(task_names, edges)
    if cycle:
        errors.append(f"{path}.{TASKS_PATH} contains a dependency cycle: {' -> '.join(cycle)}")


def _validate_component_ref(ref: Mapping[str, Any], path: str, errors: list[str]) -> None:
    """Append errors for malformed componentRef selector fields.

    A component reference must provide at least one selector or embedded spec;
    embedded ``spec`` must be object-shaped when present.
    """

    has_selector = any(ref.get(key) for key in ("name", "digest", "tag", "url", "text"))
    nested_spec = ref.get("spec")
    if nested_spec is not None and not isinstance(nested_spec, Mapping):
        errors.append(f"{path}.spec must be an object when provided")
    if isinstance(nested_spec, Mapping):
        has_selector = True
    if not has_selector:
        errors.append(
            f"{path} must include at least one of name, digest, tag, url, text, or spec"
        )



def _find_cycle(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[str]:
    """Return one dependency cycle path from nodes and directed edges.

    The returned list repeats the first cycle node at the end, or is empty when
    the graph is acyclic.
    """

    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)

    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        """Depth-first search from node, returning a cycle path when found."""

        if node in visited:
            return None
        if node in visiting:
            try:
                start = stack.index(node)
            except ValueError:
                return [node, node]
            return stack[start:] + [node]

        visiting.add(node)
        stack.append(node)
        for neighbor in sorted(adjacency.get(node, [])):
            cycle = visit(neighbor)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None

    for node in sorted(adjacency):
        cycle = visit(node)
        if cycle:
            return cycle
    return []

