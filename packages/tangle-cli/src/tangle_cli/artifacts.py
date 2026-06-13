"""Read-only artifact lookup helpers for Tangle pipeline runs.

This module intentionally resolves artifact metadata only. It does not fetch
signed URLs, download remote objects, write local files, or mutate artifacts.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .models import ArtifactComponentQuery, ArtifactInfo

if TYPE_CHECKING:
    from .client import TangleApiClient


class ArtifactClient(Protocol):
    """Subset of the static API client used for read-only artifact lookup."""

    def get_run_details(self, run_id: str) -> Any: ...

    def get_execution_details(self, execution_id: str) -> Any: ...

    def artifacts_get(self, artifact_id: str) -> Any: ...


def _mapping_or_attr(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _artifact_id_map(raw_artifacts: Any) -> dict[str, str]:
    """Normalize API artifact maps to ``{output_name: artifact_id}``."""

    if not isinstance(raw_artifacts, dict):
        return {}

    artifact_ids: dict[str, str] = {}
    for output_name, value in raw_artifacts.items():
        if isinstance(value, str):
            artifact_ids[str(output_name)] = value
        elif isinstance(value, dict) and value.get("id"):
            artifact_ids[str(output_name)] = str(value["id"])
        elif getattr(value, "id", None):
            artifact_ids[str(output_name)] = str(value.id)
    return artifact_ids


def _collect_artifacts(
    execution: Any,
    tasks_query: dict[str, list[str]],
    components_query: list[ArtifactComponentQuery],
    prefix: str = "",
) -> dict[str, str]:
    """Collect artifact IDs by walking an enriched execution tree.

    Handles both direct task matches and component matches at any nesting level.
    Returns a dict mapping ``"path/to/task/output_name"`` to artifact ID.
    """

    artifact_ids: dict[str, str] = {}
    task_spec = _mapping_or_attr(execution, "task_spec")
    graph_tasks = _mapping_or_attr(task_spec, "graph_tasks", {})
    if not isinstance(graph_tasks, dict):
        return artifact_ids

    for task_name, child_task in graph_tasks.items():
        task_name = str(task_name)
        key_prefix = f"{prefix}{task_name}" if prefix else task_name
        output_filter: list[str] = []
        matched = False

        for query_name in (task_name, key_prefix):
            if query_name in tasks_query:
                output_filter = tasks_query[query_name]
                matched = True
                break

        child_digest = _mapping_or_attr(child_task, "digest")
        child_name = _mapping_or_attr(child_task, "name")
        for component in components_query:
            if (component.digest and child_digest == component.digest) or (
                component.name and child_name == component.name
            ):
                output_filter = component.outputs if component.outputs else output_filter
                matched = True

        out_artifacts = _artifact_id_map(_mapping_or_attr(child_task, "execution_output_artifacts", {}))
        if matched and out_artifacts:
            for output_name, artifact_id in out_artifacts.items():
                if not output_filter or output_name in output_filter:
                    artifact_ids[f"{key_prefix}/{output_name}"] = artifact_id

        if _mapping_or_attr(child_task, "is_graph", False):
            child_executions = _mapping_or_attr(execution, "child_executions", {})
            child_execution = child_executions.get(task_name) if isinstance(child_executions, dict) else None
            if child_execution:
                artifact_ids.update(
                    _collect_artifacts(
                        child_execution,
                        tasks_query,
                        components_query,
                        prefix=f"{key_prefix}/",
                    )
                )

    return artifact_ids


def _collect_execution_artifacts(
    client: ArtifactClient,
    execution_ids: dict[str, list[str]],
) -> dict[str, str]:
    """Collect artifact IDs directly from execution IDs.

    Fetches each execution's details and extracts output artifacts, avoiding the
    full run-details tree walk.
    """

    artifact_ids: dict[str, str] = {}
    for execution_id, output_filter in execution_ids.items():
        execution = client.get_execution_details(execution_id)
        output_artifacts = _artifact_id_map(_mapping_or_attr(execution, "output_artifacts", {}))
        for output_name, artifact_id in output_artifacts.items():
            if not output_filter or output_name in output_filter:
                artifact_ids[f"{execution_id}/{output_name}"] = artifact_id
    return artifact_ids


def _component_queries(raw_components: list[dict[str, Any]]) -> list[ArtifactComponentQuery]:
    return [
        ArtifactComponentQuery(
            name=component.get("name"),
            digest=component.get("digest"),
            outputs=component.get("outputs") or [],
        )
        for component in raw_components
    ]


def _artifact_info_from_response(response: Any, *, artifact_id: str, key: str) -> ArtifactInfo:
    if isinstance(response, dict):
        return ArtifactInfo.from_dict(response, key=key)
    return ArtifactInfo.from_response(response, key=key)


def get_artifacts(
    run_id: str,
    query: dict[str, Any],
    client: ArtifactClient,
) -> dict[str, ArtifactInfo]:
    """Get artifact metadata for tasks/components in a pipeline run.

    Query keys:
      - ``tasks``: ``{<task_name>: [<output_names>]}``
      - ``components``: ``[{"name"|"digest": ..., "outputs": [...]}]``
      - ``executions``: ``{<execution_id>: [<output_names>]}``
      - ``artifact_ids``: ``[<artifact_id>, ...]``

    Empty output lists mean all outputs. Per-artifact lookup failures are
    returned as ``ArtifactInfo(error=...)`` entries instead of failing the whole
    command.
    """

    artifact_ids: dict[str, str] = {}

    for artifact_id in query.get("artifact_ids", []) or []:
        artifact_ids[str(artifact_id)] = str(artifact_id)

    executions_query = query.get("executions", {}) or {}
    if executions_query:
        artifact_ids.update(_collect_execution_artifacts(client, executions_query))

    tasks_query = query.get("tasks", {}) or {}
    components_query_raw = query.get("components", []) or []
    if tasks_query or components_query_raw:
        details = client.get_run_details(run_id)
        execution = _mapping_or_attr(details, "execution")
        if not execution:
            raise RuntimeError("No execution details found for run")
        artifact_ids.update(
            _collect_artifacts(
                execution,
                tasks_query,
                _component_queries(components_query_raw),
            )
        )

    artifacts: dict[str, ArtifactInfo] = {}
    for key, artifact_id in artifact_ids.items():
        try:
            response = client.artifacts_get(artifact_id)
            artifacts[key] = _artifact_info_from_response(response, artifact_id=artifact_id, key=key)
        except Exception as exc:
            artifacts[key] = ArtifactInfo(id=artifact_id, uri="", key=key, error=str(exc))

    return artifacts


def _serialize_artifacts(artifacts: dict[str, ArtifactInfo]) -> list[dict[str, Any]]:
    """Serialize artifact dict to a JSON-friendly list, dropping ``None`` fields."""

    result: list[dict[str, Any]] = []
    for artifact in artifacts.values():
        data = asdict(artifact) if is_dataclass(artifact) else dict(artifact)
        result.append({key: value for key, value in data.items() if value is not None})
    return result
