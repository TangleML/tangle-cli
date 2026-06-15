from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from tangle_cli.artifacts import get_artifacts, _serialize_artifacts


def _artifact_response(artifact_id: str, uri: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=artifact_id,
        artifact_data=SimpleNamespace(
            uri=uri,
            total_size=12,
            is_dir=False,
            hash="abc123",
            created_at="2026-01-01T00:00:00Z",
        ),
    )


class FakeArtifactClient:
    def __init__(self) -> None:
        self.artifact_responses: dict[str, Any] = {}
        self.artifact_errors: dict[str, Exception] = {}
        self.execution_details: dict[str, Any] = {}
        self.run_details: dict[str, Any] = {}
        self.artifacts_get_calls: list[str] = []
        self.get_execution_details_calls: list[str] = []
        self.get_run_details_calls: list[str] = []

    def artifacts_get(self, artifact_id: str) -> Any:
        self.artifacts_get_calls.append(artifact_id)
        if artifact_id in self.artifact_errors:
            raise self.artifact_errors[artifact_id]
        return self.artifact_responses[artifact_id]

    def get_execution_details(self, execution_id: str) -> Any:
        self.get_execution_details_calls.append(execution_id)
        return self.execution_details[execution_id]

    def get_run_details(self, run_id: str) -> Any:
        self.get_run_details_calls.append(run_id)
        return self.run_details[run_id]


def _task(
    *,
    name: str = "Component",
    digest: str | None = None,
    output_artifacts: dict[str, Any] | None = None,
    is_graph: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        digest=digest,
        execution_output_artifacts=output_artifacts or {},
        is_graph=is_graph,
    )


def _execution(graph_tasks: dict[str, Any], child_executions: dict[str, Any] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        task_spec=SimpleNamespace(graph_tasks=graph_tasks),
        child_executions=child_executions or {},
    )


def test_get_artifacts_resolves_direct_artifact_ids_without_run_tree() -> None:
    client = FakeArtifactClient()
    client.artifact_responses = {
        "artifact-1": _artifact_response("artifact-1", "gs://bucket/artifact-1"),
        "artifact-2": _artifact_response("artifact-2", "gs://bucket/artifact-2"),
    }

    artifacts = get_artifacts(
        "run-1",
        {"artifact_ids": ["artifact-1", "artifact-2"]},
        client=client,
    )

    assert list(artifacts) == ["artifact-1", "artifact-2"]
    assert artifacts["artifact-1"].uri == "gs://bucket/artifact-1"
    assert client.artifacts_get_calls == ["artifact-1", "artifact-2"]
    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []


def test_get_artifacts_resolves_execution_output_lookup() -> None:
    client = FakeArtifactClient()
    client.execution_details["exec-1"] = SimpleNamespace(
        output_artifacts={"model": {"id": "artifact-model"}, "metrics": {"id": "artifact-metrics"}}
    )
    client.artifact_responses["artifact-model"] = _artifact_response("artifact-model", "gs://bucket/model")

    artifacts = get_artifacts("run-1", {"executions": {"exec-1": ["model"]}}, client=client)

    assert list(artifacts) == ["exec-1/model"]
    assert artifacts["exec-1/model"].id == "artifact-model"
    assert client.get_execution_details_calls == ["exec-1"]
    assert client.artifacts_get_calls == ["artifact-model"]


def test_get_artifacts_resolves_task_query_from_run_details_tree() -> None:
    client = FakeArtifactClient()
    client.run_details["run-1"] = SimpleNamespace(
        execution=_execution(
            {
                "Train": _task(
                    name="Trainer",
                    output_artifacts={"model": "artifact-model", "metrics": "artifact-metrics"},
                )
            }
        )
    )
    client.artifact_responses["artifact-model"] = _artifact_response("artifact-model", "gs://bucket/model")

    artifacts = get_artifacts("run-1", {"tasks": {"Train": ["model"]}}, client=client)

    assert list(artifacts) == ["Train/model"]
    assert artifacts["Train/model"].uri == "gs://bucket/model"
    assert client.get_run_details_calls == ["run-1"]


def test_get_artifacts_resolves_component_name_and_digest_queries() -> None:
    client = FakeArtifactClient()
    client.run_details["run-1"] = SimpleNamespace(
        execution=_execution(
            {
                "Embed": _task(name="Embed Text", digest="sha256:embed", output_artifacts={"vectors": "artifact-vectors"}),
                "Score": _task(name="Score", digest="sha256:score", output_artifacts={"scores": "artifact-scores"}),
            }
        )
    )
    client.artifact_responses["artifact-vectors"] = _artifact_response("artifact-vectors", "gs://bucket/vectors")
    client.artifact_responses["artifact-scores"] = _artifact_response("artifact-scores", "gs://bucket/scores")

    artifacts = get_artifacts(
        "run-1",
        {
            "components": [
                {"name": "Embed Text", "outputs": ["vectors"]},
                {"digest": "sha256:score"},
            ]
        },
        client=client,
    )

    assert list(artifacts) == ["Embed/vectors", "Score/scores"]
    assert artifacts["Embed/vectors"].id == "artifact-vectors"
    assert artifacts["Score/scores"].id == "artifact-scores"


def test_get_artifacts_unions_outputs_from_multiple_matching_selectors() -> None:
    client = FakeArtifactClient()
    client.run_details["run-1"] = SimpleNamespace(
        execution=_execution(
            {
                "Train": _task(
                    name="Trainer",
                    digest="sha256:trainer",
                    output_artifacts={"model": "artifact-model", "metrics": "artifact-metrics"},
                )
            }
        )
    )
    client.artifact_responses["artifact-model"] = _artifact_response("artifact-model", "gs://bucket/model")
    client.artifact_responses["artifact-metrics"] = _artifact_response("artifact-metrics", "gs://bucket/metrics")

    artifacts = get_artifacts(
        "run-1",
        {
            "tasks": {"Train": ["model"]},
            "components": [{"digest": "sha256:trainer", "outputs": ["metrics"]}],
        },
        client=client,
    )

    assert list(artifacts) == ["Train/model", "Train/metrics"]
    assert artifacts["Train/model"].id == "artifact-model"
    assert artifacts["Train/metrics"].id == "artifact-metrics"


def test_get_artifacts_resolves_nested_subgraph_task_paths() -> None:
    client = FakeArtifactClient()
    nested_execution = _execution(
        {"Inner": _task(name="Inner Component", output_artifacts={"out": "artifact-inner"})}
    )
    client.run_details["run-1"] = SimpleNamespace(
        execution=_execution(
            {"Subgraph": _task(name="Subgraph", is_graph=True)},
            child_executions={"Subgraph": nested_execution},
        )
    )
    client.artifact_responses["artifact-inner"] = _artifact_response("artifact-inner", "gs://bucket/inner")

    artifacts = get_artifacts("run-1", {"tasks": {"Subgraph/Inner": ["out"]}}, client=client)

    assert list(artifacts) == ["Subgraph/Inner/out"]
    assert artifacts["Subgraph/Inner/out"].uri == "gs://bucket/inner"


def test_get_artifacts_serializes_per_artifact_lookup_errors() -> None:
    client = FakeArtifactClient()
    client.artifact_responses["good"] = _artifact_response("good", "gs://bucket/good")
    client.artifact_errors["bad"] = RuntimeError("not found")

    artifacts = get_artifacts("run-1", {"artifact_ids": ["good", "bad"]}, client=client)

    assert artifacts["good"].uri == "gs://bucket/good"
    assert artifacts["bad"].id == "bad"
    assert artifacts["bad"].uri == ""
    assert artifacts["bad"].error == "not found"

    serialized = _serialize_artifacts(artifacts)
    assert {entry["key"]: entry for entry in serialized}["bad"] == {
        "id": "bad",
        "uri": "",
        "key": "bad",
        "total_size": 0,
        "is_dir": False,
        "error": "not found",
    }


def test_get_artifacts_requires_execution_details_for_task_queries() -> None:
    client = FakeArtifactClient()
    client.run_details["run-1"] = SimpleNamespace(execution=None)

    with pytest.raises(RuntimeError, match="No execution details"):
        get_artifacts("run-1", {"tasks": {"Train": []}}, client=client)
