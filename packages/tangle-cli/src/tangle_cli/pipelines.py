"""Local helpers for working with Tangle pipeline component specs.

This module intentionally stays API-free: it validates, diagrams, and lays out
pipeline YAML files that are already present on disk.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from .utils import dump_yaml

PIPELINE_GRAPH_PATH = "implementation.graph"
TASKS_PATH = f"{PIPELINE_GRAPH_PATH}.tasks"
POSITION_ANNOTATION = "editor.position"


class PipelineValidationError(ValueError):
    """Raised when a local pipeline spec cannot be parsed or validated."""


@dataclass(frozen=True)
class LayoutResult:
    """Summary of a layout operation."""

    output_path: Path
    tasks_positioned: int
    graphs_positioned: int


@dataclass(frozen=True)
class HydrateResult:
    """Summary of a hydrate operation."""

    content: str
    output_path: Path | None
    resolved_components: int


@dataclass(frozen=True)
class _LoadedComponent:
    digest: str
    spec: dict[str, Any]
    base_dir: Path


# ---------------------------------------------------------------------------
# YAML loading / validation
# ---------------------------------------------------------------------------


def load_pipeline_file(path: str | Path) -> dict[str, Any]:
    """Load a pipeline YAML file and return its top-level mapping.

    Raises:
        PipelineValidationError: If the file cannot be read, parsed, or does
            not contain a top-level mapping.
    """

    pipeline_path = Path(path)
    try:
        text = pipeline_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PipelineValidationError(f"Unable to read {pipeline_path}: {exc}") from exc

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PipelineValidationError(f"Invalid YAML in {pipeline_path}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise PipelineValidationError("Pipeline YAML must contain a top-level mapping")

    return loaded


def validate_pipeline_file(path: str | Path) -> dict[str, Any]:
    """Load and validate a pipeline YAML file, returning the parsed spec."""

    pipeline = load_pipeline_file(path)
    validate_pipeline_spec(pipeline)
    return pipeline


def validate_pipeline_spec(pipeline: Mapping[str, Any]) -> None:
    """Validate the OSS-compatible local pipeline shape.

    This is a pragmatic validator for local authoring workflows. It focuses on
    the graph structure that the CLI commands consume rather than provider-specific
    deployment extensions or remote API fields.
    """

    errors: list[str] = []
    _validate_root_pipeline(pipeline, errors)
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise PipelineValidationError(f"Pipeline validation failed:\n{details}")


def _validate_root_pipeline(pipeline: Mapping[str, Any], errors: list[str]) -> None:
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


def _extract_task_output_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        task_output = value.get("taskOutput")
        if isinstance(task_output, Mapping) and isinstance(task_output.get("taskId"), str):
            refs.add(task_output["taskId"])
        for nested in value.values():
            refs.update(_extract_task_output_refs(nested))
    elif isinstance(value, list):
        for item in value:
            refs.update(_extract_task_output_refs(item))
    return refs


def _find_cycle(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> list[str]:
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    for source, target in edges:
        adjacency.setdefault(source, []).append(target)

    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
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


# ---------------------------------------------------------------------------
# Mermaid diagrams
# ---------------------------------------------------------------------------


def generate_mermaid(pipeline_spec: Mapping[str, Any], name: str | None = None) -> str:
    """Generate GitHub-compatible Mermaid diagrams for a pipeline spec."""

    display_name = name or str(pipeline_spec.get("name") or "Pipeline")
    tasks = _tasks_for_spec(pipeline_spec)
    if not tasks:
        return f"No tasks found in pipeline `{display_name}`."

    sections = [f"### {display_name}\n", _render_mermaid_graph(tasks)]
    for heading_level, task_id, nested_spec in _iter_nested_graph_specs(tasks, level=3):
        nested_name = str(nested_spec.get("name") or task_id)
        sections.append(f"\n{'#' * heading_level} Subgraph: {nested_name} (`{task_id}`)\n")
        sections.append(_render_mermaid_graph(_tasks_for_spec(nested_spec)))
    return "\n".join(sections)


def _render_mermaid_graph(tasks: Mapping[str, Any]) -> str:
    lines = ["```mermaid", "flowchart LR"]

    for task_id, task_spec in tasks.items():
        label = _task_label(str(task_id), task_spec)
        lines.append(f"    {_safe_mermaid_id(str(task_id))}[\"{_escape_mermaid_label(label)}\"]")

    edges = _dependency_edges(tasks)
    if edges:
        lines.append("")
        for source, target in sorted(edges):
            lines.append(f"    {_safe_mermaid_id(source)} --> {_safe_mermaid_id(target)}")

    lines.append("```")
    return "\n".join(lines)


def _iter_nested_graph_specs(
    tasks: Mapping[str, Any],
    *,
    level: int,
) -> Iterable[tuple[int, str, Mapping[str, Any]]]:
    for task_id, task_spec in tasks.items():
        if not isinstance(task_spec, Mapping):
            continue
        spec = _component_ref_spec(task_spec)
        if spec is None or not _tasks_for_spec(spec):
            continue
        yield level, str(task_id), spec
        yield from _iter_nested_graph_specs(_tasks_for_spec(spec), level=level + 1)


def _task_label(task_id: str, task_spec: Any) -> str:
    if not isinstance(task_spec, Mapping):
        return task_id

    component_ref = task_spec.get("componentRef")
    ref_name = component_ref.get("name") if isinstance(component_ref, Mapping) else None
    spec = _component_ref_spec(task_spec)
    spec_name = spec.get("name") if spec is not None else None
    label = str(ref_name or spec_name or task_id)
    if spec is not None and _tasks_for_spec(spec):
        return f"{label} [subgraph]"
    return label


def _safe_mermaid_id(task_id: str) -> str:
    safe_id = re.sub(r"\W+", "_", task_id).strip("_") or "task"
    if safe_id[0].isdigit():
        safe_id = f"task_{safe_id}"
    return safe_id


def _escape_mermaid_label(label: str) -> str:
    return label.replace("\\", "\\\\").replace('"', "\\\"")


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------


def hydrate_pipeline_file(
    pipeline_path: str | Path,
    *,
    output: str | Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | None = None,
    include_env_credentials: bool = True,
    client: Any | None = None,
    logger: Any | None = None,
    trusted_python_sources: list[str] | None = None,
    allow_all_hydration: bool = False,
) -> HydrateResult:
    """Hydrate a local pipeline YAML file using the ported TD hydrator."""

    from .pipeline_hydrator import HydrationError, PipelineHydrator

    output_path = Path(output) if output is not None else None
    try:
        hydrator = PipelineHydrator(
            client=client,
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
            include_env_credentials=include_env_credentials,
            logger=logger,
            resolution_overrides=dict(overrides or {}),
            trusted_python_sources=trusted_python_sources,
            allow_all_hydration=allow_all_hydration,
        )
        hydrated = hydrator.hydrate_file(
            pipeline_path,
            output_file=output_path,
            overrides={str(key): str(value) for key, value in (overrides or {}).items()},
        )
        validate_pipeline_spec(hydrated.data)
    except HydrationError as exc:
        raise PipelineValidationError(str(exc)) from exc

    return HydrateResult(
        content=hydrated.content,
        output_path=output_path,
        resolved_components=hydrated.resolved_count,
    )


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------


def layout_pipeline_file(
    pipeline_path: str | Path,
    *,
    output: str | Path | None = None,
    recursive: bool = False,
    x_spacing: int = 300,
    y_spacing: int = 120,
) -> LayoutResult:
    """Apply a deterministic left-to-right layout to a pipeline YAML file."""

    source_path = Path(pipeline_path)
    pipeline = validate_pipeline_file(source_path)
    tasks_positioned, graphs_positioned = layout_pipeline_spec(
        pipeline,
        recursive=recursive,
        x_spacing=x_spacing,
        y_spacing=y_spacing,
    )

    output_path = Path(output) if output is not None else source_path
    output_path.write_text(dump_yaml(pipeline), encoding="utf-8")
    return LayoutResult(
        output_path=output_path,
        tasks_positioned=tasks_positioned,
        graphs_positioned=graphs_positioned,
    )


def layout_pipeline_spec(
    pipeline: Mapping[str, Any],
    *,
    recursive: bool = False,
    x_spacing: int = 300,
    y_spacing: int = 120,
) -> tuple[int, int]:
    """Mutate a parsed pipeline spec with deterministic task coordinates."""

    tasks_positioned = _layout_graph_spec(pipeline, x_spacing=x_spacing, y_spacing=y_spacing)
    graphs_positioned = 1 if tasks_positioned else 0

    if recursive:
        for _task_id, nested_spec in _iter_mutable_nested_specs(_tasks_for_spec(pipeline)):
            nested_count = _layout_graph_spec(nested_spec, x_spacing=x_spacing, y_spacing=y_spacing)
            if nested_count:
                tasks_positioned += nested_count
                graphs_positioned += 1

    return tasks_positioned, graphs_positioned


def _layout_graph_spec(spec: Mapping[str, Any], *, x_spacing: int, y_spacing: int) -> int:
    tasks = _tasks_for_spec(spec)
    if not tasks:
        return 0

    layers = _task_layers(tasks)
    for layer_index, layer in enumerate(layers):
        for row_index, task_name in enumerate(layer):
            raw_task = tasks[task_name]
            if not isinstance(raw_task, dict):
                continue
            annotations = raw_task.setdefault("annotations", {})
            if not isinstance(annotations, dict):
                annotations = {}
                raw_task["annotations"] = annotations
            annotations[POSITION_ANNOTATION] = json.dumps(
                {"x": layer_index * x_spacing, "y": row_index * y_spacing}
            )
    return len(tasks)


def _task_layers(tasks: Mapping[str, Any]) -> list[list[str]]:
    task_names = [name for name in tasks.keys() if isinstance(name, str)]
    task_name_set = set(task_names)
    outgoing: dict[str, set[str]] = {name: set() for name in task_names}
    incoming_count: dict[str, int] = {name: 0 for name in task_names}

    for source, target in _dependency_edges(tasks):
        if source not in task_name_set or target not in task_name_set:
            continue
        if target not in outgoing[source]:
            outgoing[source].add(target)
            incoming_count[target] += 1

    ready = deque(name for name in task_names if incoming_count[name] == 0)
    layer_by_task: dict[str, int] = {name: 0 for name in ready}

    while ready:
        current = ready.popleft()
        for target in sorted(outgoing[current], key=task_names.index):
            layer_by_task[target] = max(layer_by_task.get(target, 0), layer_by_task[current] + 1)
            incoming_count[target] -= 1
            if incoming_count[target] == 0:
                ready.append(target)

    # Validation rejects cycles, but keep layout deterministic if called directly.
    for name in task_names:
        if name not in layer_by_task:
            layer_by_task[name] = 0

    max_layer = max(layer_by_task.values(), default=0)
    layers: list[list[str]] = [[] for _ in range(max_layer + 1)]
    for name in task_names:
        layers[layer_by_task[name]].append(name)
    return layers


def _dependency_edges(tasks: Mapping[str, Any]) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    task_names = {name for name in tasks.keys() if isinstance(name, str)}

    for task_id, task_spec in tasks.items():
        if not isinstance(task_id, str) or not isinstance(task_spec, Mapping):
            continue
        target = task_id
        dependencies = task_spec.get("dependencies", [])
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if isinstance(dependency, str) and dependency in task_names:
                    edges.add((dependency, target))
        for referenced_task in _extract_task_output_refs(task_spec.get("arguments", {})):
            if referenced_task in task_names:
                edges.add((referenced_task, target))

    return edges


def _tasks_for_spec(spec: Mapping[str, Any]) -> Mapping[str, Any]:
    implementation = spec.get("implementation")
    if not isinstance(implementation, Mapping):
        return {}
    graph = implementation.get("graph")
    if not isinstance(graph, Mapping):
        return {}
    tasks = graph.get("tasks")
    return tasks if isinstance(tasks, Mapping) else {}


def _component_ref_spec(task_spec: Mapping[str, Any]) -> Mapping[str, Any] | None:
    component_ref = task_spec.get("componentRef")
    if not isinstance(component_ref, Mapping):
        return None
    spec = component_ref.get("spec")
    return spec if isinstance(spec, Mapping) else None


def _iter_mutable_nested_specs(tasks: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for task_id, task_spec in tasks.items():
        if not isinstance(task_spec, Mapping):
            continue
        spec = _component_ref_spec(task_spec)
        if spec is None or not _tasks_for_spec(spec):
            continue
        yield str(task_id), spec
        yield from _iter_mutable_nested_specs(_tasks_for_spec(spec))
