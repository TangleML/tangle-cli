"""Local helpers for working with Tangle pipeline component specs.

This module intentionally stays API-free: it loads, hydrates, diagrams, and lays
out pipeline YAML files that are already present on disk. Pipeline validation
lives in :mod:`tangle_cli.pipeline_validation` and is re-exported here for
backward compatibility.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

import yaml

from .pipeline_spec_utils import _extract_task_output_refs
from .pipeline_validation import (
    PipelineValidationError,
    collect_pipeline_spec_errors,
    load_pipeline_schema,
    validate_component_inputs,
    validate_pipeline_schema,
    validate_pipeline_spec,
)
from .utils import dump_yaml

if TYPE_CHECKING:
    from .pipeline_compiler import CompileResult

POSITION_ANNOTATION = "editor.position"

__all__ = [
    "HydrateResult",
    "LayoutResult",
    "PipelineValidationError",
    "collect_pipeline_spec_errors",
    "generate_mermaid",
    "hydrate_pipeline_file",
    "layout_pipeline_file",
    "layout_pipeline_spec",
    "load_pipeline_file",
    "load_pipeline_schema",
    "validate_component_inputs",
    "validate_pipeline_file",
    "validate_pipeline_schema",
    "validate_pipeline_spec",
]


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
# Compile
# ---------------------------------------------------------------------------


def compile_pipeline_file(
    script: str | Path,
    output: str | Path,
    *,
    overrides: Mapping[str, str] | None = None,
    pipeline_name: str | None = None,
    emit_components_sidecar: bool = True,
    image_overrides: Mapping[str, str] | None = None,
    logger: Any | None = None,
) -> CompileResult:
    """Compile a Python-authored pipeline to a dehydrated YAML bundle.

    Instantiates the ported :class:`~tangle_cli.pipeline_compiler.PipelineCompiler`
    handler and delegates to its ``compile_file`` method, translating the
    compiler's domain errors into :class:`PipelineValidationError` for a uniform
    CLI failure contract. Mirrors :func:`hydrate_pipeline_file`.

    The :class:`~tangle_cli.pipeline_compiler.CompileResult` is returned as-is —
    unlike hydrate, the compiler already exposes its public result type, so there
    is nothing to repackage.
    """

    from .pipeline_compiler import PipelineCompiler
    from .python_pipeline.errors import CompileError
    from .schema_validation import SchemaValidationError

    compiler = PipelineCompiler(logger=logger)
    try:
        return compiler.compile_file(
            Path(script),
            Path(output),
            overrides=dict(overrides) if overrides else None,
            pipeline_name=pipeline_name,
            emit_components_sidecar=emit_components_sidecar,
            image_overrides=dict(image_overrides) if image_overrides else None,
        )
    except (CompileError, SchemaValidationError) as exc:
        raise PipelineValidationError(str(exc)) from exc


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
