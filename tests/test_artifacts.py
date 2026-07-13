from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
import requests
from pydantic import BaseModel

from tangle_cli import artifacts as tangle_artifacts
from tangle_cli.artifacts import ArtifactManager
from tangle_cli.client import TangleApiClient


def _manager(client: Any) -> ArtifactManager:
    return ArtifactManager(client=client)


class FakeDataResponse:
    def __init__(
        self,
        content: bytes,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            # Mirror ``requests``: raise an ``HTTPError`` carrying the response so
            # the download path can surface a concise status without a traceback.
            raise requests.HTTPError(f"{self.status_code} Server Error", response=self)
        return None

    def iter_content(self, chunk_size: int = 1) -> Any:
        for start in range(0, len(self.content), max(chunk_size, 1)):
            yield self.content[start : start + chunk_size]

    def close(self) -> None:
        self.closed = True


class StreamingOnlyResponse:
    """Response that yields chunks but raises if the body is buffered via ``.content``.

    Proves the download path streams to disk and never materializes the full
    artifact in memory.
    """

    def __init__(self, chunks: list[bytes], status_code: int = 200) -> None:
        self._chunks = chunks
        self.status_code = status_code
        self.iter_calls: list[int] = []

    @property
    def content(self) -> bytes:
        raise AssertionError("streamed download must not access response.content")

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 1) -> Any:
        self.iter_calls.append(chunk_size)
        yield from self._chunks

    def close(self) -> None:
        return None


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
        self.execution_errors: dict[str, Exception] = {}
        self.run_details: dict[str, Any] = {}
        # Maps a run id -> its root execution id, mirroring the shallow
        # ``pipeline_runs_get`` run record. Unknown run ids raise a 404 so the
        # resolver falls back to treating the id as an execution id.
        self.run_records: dict[str, str] = {}
        self.artifacts_get_calls: list[str] = []
        self.get_execution_details_calls: list[str] = []
        self.get_run_details_calls: list[str] = []
        self.pipeline_runs_get_calls: list[str] = []
        self.executions_details_calls: list[str] = []
        self.data_responses: dict[str, bytes] = {}
        self.data_status_codes: dict[str, int] = {}
        self.signed_data_status_codes: dict[str, int] = {}
        self.signed_urls: dict[str, str] = {}
        self.signed_url_errors: dict[str, Exception] = {}
        self.make_request_calls: list[tuple[str, str]] = []
        self.stream_responses: dict[str, StreamingOnlyResponse] = {}
        self.artifacts_signed_url_calls: list[str] = []

    def artifacts_get(self, artifact_id: str) -> Any:
        self.artifacts_get_calls.append(artifact_id)
        if artifact_id in self.artifact_errors:
            raise self.artifact_errors[artifact_id]
        return self.artifact_responses[artifact_id]

    def get_execution_details(self, execution_id: str) -> Any:
        self.get_execution_details_calls.append(execution_id)
        if execution_id in self.execution_errors:
            raise self.execution_errors[execution_id]
        return self.execution_details[execution_id]

    def pipeline_runs_get(self, run_id: str) -> Any:
        self.pipeline_runs_get_calls.append(run_id)
        if run_id in self.run_records:
            return SimpleNamespace(root_execution_id=self.run_records[run_id])
        # No run record: mirror the real client raising 404 so the resolver
        # falls back to treating the id as an execution id.
        raise requests.HTTPError(
            "404 Not Found", response=FakeDataResponse(b"", status_code=404)
        )

    def executions_details(self, execution_id: str) -> Any:
        self.executions_details_calls.append(execution_id)
        if execution_id in self.execution_errors:
            raise self.execution_errors[execution_id]
        return self.execution_details[execution_id]

    def get_run_details(self, run_id: str) -> Any:
        self.get_run_details_calls.append(run_id)
        if run_id in self.run_details:
            return self.run_details[run_id]
        if run_id in self.execution_errors:
            raise self.execution_errors[run_id]
        # Model the client's run->root-execution resolution: with no explicit
        # run record, fall back to treating the id as the root execution id.
        return SimpleNamespace(
            run=SimpleNamespace(root_execution_id=run_id),
            execution=self.execution_details.get(run_id),
        )

    def request_raw(self, method: str, path: str, **kwargs: Any) -> Any:
        self.make_request_calls.append((method, path))
        if path.startswith("http"):
            return FakeDataResponse(self.data_responses[path])
        if path.endswith("/signed-data"):
            artifact_id = path.removeprefix("/api/artifacts/").removesuffix("/signed-data")
            return FakeDataResponse(
                self.data_responses.get(artifact_id, b""),
                self.signed_data_status_codes.get(artifact_id, 200),
            )
        artifact_id = path.removeprefix("/api/artifacts/").removesuffix("/data")
        if artifact_id in self.stream_responses:
            return self.stream_responses[artifact_id]
        return FakeDataResponse(
            self.data_responses.get(artifact_id, b""),
            self.data_status_codes.get(artifact_id, 200),
        )

    def artifacts_signed_artifact_url(self, artifact_id: str) -> Any:
        self.artifacts_signed_url_calls.append(artifact_id)
        if artifact_id in self.signed_url_errors:
            raise self.signed_url_errors[artifact_id]
        return SimpleNamespace(signed_url=self.signed_urls[artifact_id])


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


def _execution(
    graph_tasks: dict[str, Any],
    child_executions: dict[str, Any] | None = None,
    output_artifacts: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        task_spec=SimpleNamespace(graph_tasks=graph_tasks),
        child_executions=child_executions or {},
        output_artifacts=output_artifacts or {},
    )


def _single_root_artifact_client(artifact_id: str = "artifact-model") -> FakeArtifactClient:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": artifact_id}},
    )
    return client


# ---------------------------------------------------------------------------
# Metadata query resolution
# ---------------------------------------------------------------------------


def test_get_artifacts_resolves_direct_artifact_ids_without_run_tree() -> None:
    client = FakeArtifactClient()
    client.artifact_responses = {
        "artifact-1": _artifact_response("artifact-1", "gs://bucket/artifact-1"),
        "artifact-2": _artifact_response("artifact-2", "gs://bucket/artifact-2"),
    }

    artifacts = _manager(client).get_artifacts(
        "run-1",
        {"artifact_ids": ["artifact-1", "artifact-2"]},
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

    artifacts = _manager(client).get_artifacts("run-1", {"executions": {"exec-1": ["model"]}})

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

    artifacts = _manager(client).get_artifacts("run-1", {"tasks": {"Train": ["model"]}})

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

    artifacts = _manager(client).get_artifacts(
        "run-1",
        {
            "components": [
                {"name": "Embed Text", "outputs": ["vectors"]},
                {"digest": "sha256:score"},
            ]
        },
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

    artifacts = _manager(client).get_artifacts(
        "run-1",
        {
            "tasks": {"Train": ["model"]},
            "components": [{"digest": "sha256:trainer", "outputs": ["metrics"]}],
        },
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

    artifacts = _manager(client).get_artifacts("run-1", {"tasks": {"Subgraph/Inner": ["out"]}})

    assert list(artifacts) == ["Subgraph/Inner/out"]
    assert artifacts["Subgraph/Inner/out"].uri == "gs://bucket/inner"


def test_get_artifacts_serializes_per_artifact_lookup_errors() -> None:
    client = FakeArtifactClient()
    client.artifact_responses["good"] = _artifact_response("good", "gs://bucket/good")
    client.artifact_errors["bad"] = RuntimeError("not found")

    artifacts = _manager(client).get_artifacts("run-1", {"artifact_ids": ["good", "bad"]})

    assert artifacts["good"].uri == "gs://bucket/good"
    assert artifacts["bad"].id == "bad"
    assert artifacts["bad"].uri == ""
    assert artifacts["bad"].error == "not found"

    serialized = ArtifactManager.serialize_artifacts(artifacts)
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
        _manager(client).get_artifacts("run-1", {"tasks": {"Train": []}})


@pytest.mark.parametrize("query", [[1, 2, 3], "a string", 42])
def test_get_artifacts_rejects_non_object_query_without_network(query: Any) -> None:
    client = FakeArtifactClient()

    with pytest.raises(RuntimeError, match="--query must be a JSON object"):
        _manager(client).get_artifacts("run-1", query)

    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []
    assert client.artifacts_get_calls == []


@pytest.mark.parametrize(
    "query, match",
    [
        ({"executions": [1]}, "'executions' must be an object"),
        ({"executions": {"exec-1": "model"}}, "'executions' entry 'exec-1' must be a list"),
        ({"executions": {"exec-1": [1]}}, "'executions' entry 'exec-1' must be a list"),
        ({"tasks": ["Train"]}, "'tasks' must be an object"),
        ({"tasks": {"Train": "model"}}, "'tasks' entry 'Train' must be a list"),
        ({"components": {"name": "Train"}}, "'components' must be a list"),
        ({"components": ["Train"]}, "'components' entry 0 must be an object"),
        ({"components": [{"name": 1}]}, "'components' entry 0 key 'name' must be a string"),
        ({"components": [{"digest": 1}]}, "'components' entry 0 key 'digest' must be a string"),
        (
            {"components": [{"name": "Train", "outputs": "model"}]},
            "'components' entry 0 key 'outputs' must be a list",
        ),
        ({"artifact_ids": "artifact-1"}, "'artifact_ids' must be a list"),
        ({"artifact_ids": [1]}, "'artifact_ids' entry 0 must be a string"),
        ({"artifact_ids": [None]}, "'artifact_ids' entry 0 must be a string"),
    ],
)
def test_get_artifacts_rejects_malformed_nested_query_without_network(
    query: dict[str, Any], match: str
) -> None:
    # Nested shapes are checked up front: a malformed inner value must raise a
    # clean RuntimeError, not an AttributeError/TypeError traceback from deep
    # inside the artifact walk (e.g. ``.items()`` on a list).
    client = FakeArtifactClient()

    with pytest.raises(RuntimeError, match=match):
        _manager(client).get_artifacts("run-1", query)

    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []
    assert client.artifacts_get_calls == []


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"tasks": None, "executions": None, "components": None, "artifact_ids": None},
    ],
)
def test_get_artifacts_accepts_empty_query_object_without_network(
    query: dict[str, Any],
) -> None:
    # An explicit empty object (and explicit nulls, which mean "absent") is a
    # valid query that selects nothing and touches no endpoint.
    client = FakeArtifactClient()

    assert _manager(client).get_artifacts("run-1", query) == {}

    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []
    assert client.artifacts_get_calls == []


@pytest.mark.parametrize(
    "query, expected_keys",
    [
        # Regression: a null tasks filter passed validation but raised a raw
        # ``TypeError: 'NoneType' object is not iterable`` from the artifact
        # walk once the named task matched and had output artifacts.
        ({"tasks": {"Train": None}}, ["Train/model", "Train/metrics"]),
        ({"executions": {"exec-1": None}}, ["exec-1/model", "exec-1/metrics"]),
        (
            {"components": [{"name": "Trainer", "outputs": None}]},
            ["Train/model", "Train/metrics"],
        ),
    ],
)
def test_get_artifacts_treats_null_entry_output_filter_as_all_outputs(
    query: dict[str, Any], expected_keys: list[str]
) -> None:
    # A ``null`` per-entry output filter means all outputs, exactly like
    # ``[]``, for every nested query section.
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
    client.execution_details["exec-1"] = SimpleNamespace(
        output_artifacts={"model": {"id": "artifact-model"}, "metrics": {"id": "artifact-metrics"}}
    )
    client.artifact_responses["artifact-model"] = _artifact_response("artifact-model", "gs://bucket/model")
    client.artifact_responses["artifact-metrics"] = _artifact_response(
        "artifact-metrics", "gs://bucket/metrics"
    )

    artifacts = _manager(client).get_artifacts("run-1", query)

    assert list(artifacts) == expected_keys
    assert all(artifact.error is None for artifact in artifacts.values())


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


def test_list_result_artifacts_includes_root_and_direct_children() -> None:
    client = FakeArtifactClient()
    child_execution = _execution(
        {},
        output_artifacts={"child output": {"id": "artifact-child"}},
    )
    client.execution_details["root-exec"] = _execution(
        {},
        child_executions={"Train": child_execution},
        output_artifacts={"final": {"id": "artifact-root"}},
    )

    artifacts = _manager(client).list_result_artifacts("root-exec", include_children=True)

    assert artifacts == [
        {"owner": "root", "output": "final", "artifact_id": "artifact-root"},
        {"owner": "Train", "output": "child output", "artifact_id": "artifact-child"},
    ]
    # Root execution is resolved shallowly (run lookup 404s -> treated as an
    # execution id -> one executions_details); direct children are inline so no
    # per-child fetch is needed, and the enriching helpers are never used.
    assert client.pipeline_runs_get_calls == ["root-exec"]
    assert client.executions_details_calls == ["root-exec"]
    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []


def test_list_result_artifacts_fetches_direct_children_from_raw_child_ids() -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = SimpleNamespace(
        output_artifacts={"final": {"id": "artifact-root"}},
        child_task_execution_ids={"Train": "child-exec"},
    )
    client.execution_details["child-exec"] = SimpleNamespace(
        output_artifacts={"child": {"id": "artifact-child"}}
    )

    artifacts = _manager(client).list_result_artifacts("root-exec", include_children=True)

    assert artifacts == [
        {"owner": "root", "output": "final", "artifact_id": "artifact-root"},
        {"owner": "Train", "output": "child", "artifact_id": "artifact-child"},
    ]
    # Root and the single raw child id are each fetched once, shallowly, via
    # executions_details; the recursive get_execution_details is never used.
    assert client.pipeline_runs_get_calls == ["root-exec"]
    assert client.executions_details_calls == ["root-exec", "child-exec"]
    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []


def test_list_result_artifacts_resolves_run_id_to_root_execution() -> None:
    # The CLI accepts a run id, but the artifact tree lives under the run's root
    # execution (a distinct id). The run id must be mapped to its root execution
    # id via the run lookup, then that execution fetched — the run id is never
    # passed straight through as an execution id.
    client = FakeArtifactClient()
    client.run_records["run-1"] = "root-exec-9"
    client.execution_details["root-exec-9"] = _execution(
        {}, output_artifacts={"final": {"id": "artifact-root"}}
    )

    rows = _manager(client).list_result_artifacts("run-1")

    assert rows == [{"owner": "root", "output": "final", "artifact_id": "artifact-root"}]
    assert client.pipeline_runs_get_calls == ["run-1"]
    assert client.executions_details_calls == ["root-exec-9"]
    # The run id is never conflated with an execution id.
    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []


def test_list_result_artifacts_missing_execution_for_run_is_clean_error() -> None:
    client = FakeArtifactClient()
    client.run_records["run-1"] = "root-exec-9"
    client.execution_details["root-exec-9"] = None

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).list_result_artifacts("run-1")
    assert "No execution details found for run run-1" in str(exc_info.value)


def test_list_result_artifacts_surfaces_clean_error_on_non_request_child_error() -> None:
    # A non-RequestException from get_execution_details (e.g. a client-specific
    # error) must be wrapped as a concise RuntimeError instead of escaping the
    # CLI's RuntimeError handler as a raw traceback.
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = SimpleNamespace(
        output_artifacts={},
        child_task_execution_ids={"child": "child-exec"},
    )
    client.execution_errors["child-exec"] = ValueError("unexpected client failure")

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).list_result_artifacts("root-exec", include_children=True)
    message = str(exc_info.value)
    assert "Failed to list artifacts for run root-exec" in message
    assert "Traceback" not in message


def test_list_result_artifacts_surfaces_clean_error_on_http_error() -> None:
    client = FakeArtifactClient()
    client.execution_errors["root-exec"] = requests.HTTPError(
        "403 Forbidden", response=FakeDataResponse(b"", status_code=403)
    )

    # An HTTPError propagates so the CLI's clean-error boundary formats it.
    with pytest.raises(requests.HTTPError):
        _manager(client).list_result_artifacts("root-exec")


# ---------------------------------------------------------------------------
# Download: resolution, fallbacks, dedup, filtering
# ---------------------------------------------------------------------------


def test_download_result_artifacts_resolves_run_id_to_root_execution(tmp_path) -> None:
    client = FakeArtifactClient()
    client.run_records["run-1"] = "root-exec-9"
    client.execution_details["root-exec-9"] = _execution(
        {}, output_artifacts={"model": {"id": "artifact-model"}}
    )
    client.data_responses = {"artifact-model": b'{"model": true}'}

    artifacts = _manager(client).download_result_artifacts("run-1", out_dir=tmp_path)

    assert set(artifacts) == {"root::model"}
    assert artifacts["root::model"].read_bytes() == b'{"model": true}'
    assert client.pipeline_runs_get_calls == ["run-1"]
    assert client.executions_details_calls == ["root-exec-9"]
    assert client.get_run_details_calls == []
    assert client.get_execution_details_calls == []
    assert client.make_request_calls == [("GET", "/api/artifacts/artifact-model/data")]


# ---------------------------------------------------------------------------
# Real-client call-count boundary: resolution must stay shallow and never
# recursively enrich descendants through the real TangleApiClient.
# ---------------------------------------------------------------------------


class _RecordingResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.content = json.dumps(payload).encode("utf-8")
        self.url = "https://api.test"
        self.reason = ""

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)


class _RecordingSession:
    """Session that records requested paths and rejects unregistered ones.

    An unregistered path raises immediately, so any recursive descendant fetch
    (the old ``get_execution_details`` enrichment) shows up as a hard failure
    rather than a silent extra call.
    """

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requested_paths: list[str] = []
        self.headers: dict[str, str] = {}

    def request(self, method: str, url: str, **kwargs: Any) -> _RecordingResponse:
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        self.requested_paths.append(path)
        if path not in self.routes:
            raise AssertionError(f"unexpected request: {method} {path}")
        return _RecordingResponse(self.routes[path])


def test_list_result_artifacts_real_client_is_shallow_without_children() -> None:
    # Without include_children the real client must issue exactly two shallow
    # requests (run lookup + one execution fetch). The old resolution went
    # through get_run_details -> get_execution_details, which recursively
    # enriches every descendant; here the child endpoint is deliberately
    # unregistered so any such descent fails the test loudly.
    session = _RecordingSession(
        {
            "/api/pipeline_runs/run-1": {"id": "run-1", "root_execution_id": "root-exec"},
            "/api/executions/root-exec/details": {
                "id": "root-exec",
                "output_artifacts": {"final": {"id": "artifact-root"}},
                "child_task_execution_ids": {"Train": "child-exec"},
            },
        }
    )
    client = TangleApiClient("https://api.test", session=session)

    rows = _manager(client).list_result_artifacts("run-1")

    assert rows == [{"owner": "root", "output": "final", "artifact_id": "artifact-root"}]
    assert session.requested_paths == [
        "/api/pipeline_runs/run-1",
        "/api/executions/root-exec/details",
    ]


def test_list_result_artifacts_real_client_fetches_only_direct_children() -> None:
    # With include_children the real client fetches the root and its *direct*
    # children only. The direct child itself references a grandchild execution;
    # that grandchild endpoint is unregistered, proving the shallow
    # executions_details fetch never descends into it.
    session = _RecordingSession(
        {
            "/api/pipeline_runs/run-1": {"id": "run-1", "root_execution_id": "root-exec"},
            "/api/executions/root-exec/details": {
                "id": "root-exec",
                "output_artifacts": {"final": {"id": "artifact-root"}},
                "child_task_execution_ids": {"Train": "child-exec"},
            },
            "/api/executions/child-exec/details": {
                "id": "child-exec",
                "output_artifacts": {"model": {"id": "artifact-child"}},
                "child_task_execution_ids": {"Sub": "grand-exec"},
            },
        }
    )
    client = TangleApiClient("https://api.test", session=session)

    rows = _manager(client).list_result_artifacts("run-1", include_children=True)

    assert rows == [
        {"owner": "root", "output": "final", "artifact_id": "artifact-root"},
        {"owner": "Train", "output": "model", "artifact_id": "artifact-child"},
    ]
    assert session.requested_paths == [
        "/api/pipeline_runs/run-1",
        "/api/executions/root-exec/details",
        "/api/executions/child-exec/details",
    ]


def test_download_result_artifacts_filters_and_deduplicates_by_artifact_id(tmp_path) -> None:
    client = FakeArtifactClient()
    child_execution = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-shared"}},
    )
    client.execution_details["root-exec"] = _execution(
        {},
        child_executions={"Train Task": child_execution},
        output_artifacts={
            "model": {"id": "artifact-shared"},
            "metrics": {"id": "artifact-metrics"},
        },
    )
    client.data_responses = {
        "artifact-shared": b'{"model": true}',
        "artifact-metrics": b'{"metrics": true}',
    }

    artifacts = _manager(client).download_result_artifacts(
        "root-exec",
        out_dir=tmp_path,
        only=["model"],
        include_children=True,
    )

    assert set(artifacts) == {"root::model", "Train Task::model"}
    assert artifacts["root::model"] == artifacts["Train Task::model"]
    assert artifacts["root::model"].name == "root__model__artifact-sha"
    assert artifacts["root::model"].read_bytes() == b'{"model": true}'
    assert client.make_request_calls == [("GET", "/api/artifacts/artifact-shared/data")]


def test_download_result_artifacts_falls_back_to_metadata_value_after_404(tmp_path) -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = 404
    client.artifact_responses["artifact-model"] = SimpleNamespace(
        id="artifact-model",
        artifact_data=SimpleNamespace(value="inline bytes"),
    )

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    # Inline values are JSON-encoded and gain a .json suffix at write time.
    assert artifacts["root::model"].name == "root__model__artifact-mod.json"
    assert artifacts["root::model"].read_bytes() == b'"inline bytes"'
    assert client.artifacts_get_calls == ["artifact-model"]
    assert client.make_request_calls == [("GET", "/api/artifacts/artifact-model/data")]


@pytest.mark.parametrize(
    "value, expected",
    [
        ({"nested": {"k": "v"}, "n": 1, "ok": True}, b'{"nested": {"k": "v"}, "n": 1, "ok": true}'),
        ([1, "two", {"three": 3}], b'[1, "two", {"three": 3}]'),
        (42, b"42"),
        (True, b"true"),
        # An explicit inline null is a real value and must be written as JSON
        # ``null`` — not treated as "absent" and routed to the signed URL. The
        # client below has no signed URL, so a fall-through would error out.
        (None, b"null"),
    ],
)
def test_download_json_encodes_structured_inline_value(value: Any, expected: bytes, tmp_path) -> None:
    # A structured inline metadata value (dict/list/number/bool/null) must be
    # written as valid JSON, not the Python repr that ``str()`` would produce for
    # a dict or list (single quotes, ``True``/``None`` tokens) into a ``.json`` file.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.artifact_responses["artifact-model"] = SimpleNamespace(
        id="artifact-model",
        artifact_data=SimpleNamespace(value=value),
    )

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    written = artifacts["root::model"].read_bytes()
    assert written == expected
    assert artifacts["root::model"].name.endswith(".json")
    # The written file round-trips as JSON; a str()-based repr would not.
    assert json.loads(written) == value


def test_download_absent_inline_value_falls_back_to_signed_url(tmp_path) -> None:
    # A genuinely absent ``value`` field (no such attribute) is not an inline
    # value: the download must fall through to the signed URL rather than
    # writing anything. Counterpart to the explicit inline-null case above.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.artifact_responses["artifact-model"] = SimpleNamespace(
        id="artifact-model",
        artifact_data=SimpleNamespace(),  # no ``value`` attribute
    )
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"
    client.data_responses["artifact-model"] = b"relative signed bytes"

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"relative signed bytes"
    assert client.artifacts_signed_url_calls == ["artifact-model"]
    assert client.make_request_calls == [
        ("GET", "/api/artifacts/artifact-model/data"),
        ("GET", "/api/artifacts/artifact-model/signed-data"),
    ]


class TypedArtifactData(BaseModel):
    """Pydantic stand-in mirroring the generated ``ArtifactData`` model.

    The shipped generated client deserializes ``artifact_data`` as a plain
    dict (the response field is typed ``Any``), where ``dict.get`` already
    distinguishes absent from explicit null. A custom client may return a
    typed model whose ``value`` defaults to ``None`` at class level; these
    tests pin that pydantic's set-field tracking keeps the distinction.
    """

    uri: Any = None
    value: Any = None


def test_download_typed_artifact_data_unset_value_falls_back_to_signed_url(tmp_path) -> None:
    # A typed model with ``value`` left at its class default has no inline
    # value: the ``None`` default must not be mistaken for an explicit inline
    # null and written as a ``null`` .json file.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.artifact_responses["artifact-model"] = SimpleNamespace(
        id="artifact-model",
        artifact_data=TypedArtifactData(uri="gs://bucket/model"),
    )
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"
    client.data_responses["artifact-model"] = b"signed bytes"

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"signed bytes"
    assert not artifacts["root::model"].name.endswith(".json")
    assert client.artifacts_signed_url_calls == ["artifact-model"]


def test_download_typed_artifact_data_explicit_null_written_as_json_null(tmp_path) -> None:
    # An explicitly-set ``value=None`` on a typed model is a real inline null
    # and must be written as JSON ``null``, not routed to the signed URL. The
    # client below has no signed URL, so a fall-through would error out.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.artifact_responses["artifact-model"] = SimpleNamespace(
        id="artifact-model",
        artifact_data=TypedArtifactData(value=None),
    )

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].name.endswith(".json")
    assert artifacts["root::model"].read_bytes() == b"null"
    assert client.artifacts_signed_url_calls == []


def test_download_transient_metadata_error_falls_back_to_signed_url(tmp_path) -> None:
    # A failure fetching the inline metadata value after a fallback status is a
    # best-effort step: the download must continue to the signed URL (the
    # authoritative final attempt) rather than stopping on the inline-fetch error.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.artifact_errors["artifact-model"] = requests.ConnectionError("boom")
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"
    client.data_responses["artifact-model"] = b"signed bytes"

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"signed bytes"
    assert client.artifacts_signed_url_calls == ["artifact-model"]


@pytest.mark.parametrize("status_code", [403, 410])
def test_download_result_artifacts_falls_back_to_metadata_value_for_fallback_status(
    status_code: int,
    tmp_path,
) -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = status_code
    client.artifact_responses["artifact-model"] = SimpleNamespace(
        id="artifact-model",
        artifact_data=SimpleNamespace(value="inline bytes"),
    )

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b'"inline bytes"'
    assert client.make_request_calls == [("GET", "/api/artifacts/artifact-model/data")]


@pytest.mark.parametrize("status_code", [403, 410])
def test_download_result_artifacts_falls_back_to_signed_url_for_fallback_status(
    status_code: int,
    tmp_path,
) -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = status_code
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"
    client.data_responses["artifact-model"] = b"relative signed bytes"

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"relative signed bytes"
    assert client.make_request_calls == [
        ("GET", "/api/artifacts/artifact-model/data"),
        ("GET", "/api/artifacts/artifact-model/signed-data"),
    ]


def test_download_result_artifacts_reraises_non_fallback_status(tmp_path) -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = 500

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "Artifact download failed" in message
    assert "/api/artifacts/artifact-model/data" in message
    assert "HTTP 500" in message
    assert "Traceback" not in message
    assert client.artifacts_get_calls == []


def test_download_result_artifacts_uses_client_for_relative_signed_url(tmp_path) -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"
    client.data_responses["artifact-model"] = b"relative signed bytes"

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"relative signed bytes"
    assert client.make_request_calls == [
        ("GET", "/api/artifacts/artifact-model/data"),
        ("GET", "/api/artifacts/artifact-model/signed-data"),
    ]


# ---------------------------------------------------------------------------
# Signed-URL failure handling (clean errors, no URL echo)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [403, 404])
def test_download_signed_url_fallback_failure_surfaces_clean_error(
    status_code: int,
    tmp_path,
) -> None:
    # Direct /data is forbidden/missing and the signed-URL download itself fails.
    # The user must see a concise message naming both the direct status and the
    # signed-URL status, not a raw requests traceback.
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = status_code
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"
    client.signed_data_status_codes["artifact-model"] = 500

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "artifact-model download failed" in message
    assert f"HTTP {status_code}" in message
    assert "signed-URL download returned HTTP 500" in message
    assert "Traceback" not in message


def test_download_signed_url_request_failure_surfaces_clean_error(tmp_path) -> None:
    # /data returns 403 and requesting the signed URL itself errors; the message
    # must cover both, with no traceback.
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = 403
    client.signed_url_errors["artifact-model"] = requests.HTTPError(
        "403 Forbidden", response=FakeDataResponse(b"", status_code=403)
    )

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "artifact-model download failed" in message
    assert "signed-URL request failed: HTTP 403" in message
    assert "Traceback" not in message


@pytest.mark.parametrize(
    "signed_url",
    [
        "gs://bucket/artifact-model",
        "s3://bucket/artifact-model",
        "file:///etc/passwd",
        "//storage.example/artifact-model",  # protocol-relative
    ],
)
def test_download_rejects_non_http_signed_url_cleanly(signed_url: str, tmp_path) -> None:
    # A non-http(s) or protocol-relative signed URL must be rejected as a clean
    # RuntimeError before it reaches the client's raw request (whose relative-URL
    # join raises a ValueError that would escape the CLI as a traceback). The
    # error must not echo the signed URL, which can carry credentials.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = signed_url

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "unsupported signed URL" in message
    assert "Traceback" not in message
    assert signed_url not in message
    # Only the direct /data probe was issued; the bad URL was never fetched.
    assert client.make_request_calls == [("GET", "/api/artifacts/artifact-model/data")]


# ---------------------------------------------------------------------------
# Signed-URL fetch: unauthenticated absolute GET, redirect handling
# ---------------------------------------------------------------------------


def test_download_result_artifacts_uses_unauthenticated_get_for_absolute_signed_url(
    monkeypatch,
    tmp_path,
) -> None:
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        output_artifacts={"model": {"id": "artifact-model"}},
    )
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"
    external_get_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append((url, kwargs))
        return FakeDataResponse(b"signed bytes")

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"signed bytes"
    assert external_get_calls == [
        (
            "https://storage.example/artifact-model",
            {"timeout": 60, "stream": True, "allow_redirects": False},
        )
    ]
    assert client.make_request_calls == [("GET", "/api/artifacts/artifact-model/data")]


def test_download_falls_back_to_signed_url_on_cross_origin_redirect(tmp_path, monkeypatch) -> None:
    # The direct /data route 302-redirects to cross-origin storage. The client
    # refuses to forward Tangle credentials off-origin and raises an HTTPError
    # carrying the redirect response *before* a body is returned. The download
    # must not stop at the 302: it falls back to the signed URL, which for an
    # absolute storage URL is fetched with no auth headers (no credential leak).
    client = _single_root_artifact_client()
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"

    redirect_response = FakeDataResponse(b"", status_code=302)
    original_make_request = client.request_raw

    def make_request(method: str, path: str, **kwargs: Any) -> Any:
        if path.endswith("/data"):
            raise requests.HTTPError(
                "Refusing to follow cross-origin redirect from ... to ...",
                response=redirect_response,
            )
        return original_make_request(method, path, **kwargs)

    client.request_raw = make_request  # type: ignore[method-assign]

    external_get_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append((url, kwargs))
        return FakeDataResponse(b"storage bytes")

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"storage bytes"
    # The off-origin storage URL is fetched with only timeout/stream — no auth
    # headers, cookies, or tokens are forwarded across origins.
    assert external_get_calls == [
        (
            "https://storage.example/artifact-model",
            {"timeout": 60, "stream": True, "allow_redirects": False},
        )
    ]
    assert client.artifacts_signed_url_calls == ["artifact-model"]


def test_download_signed_url_redirect_to_non_http_target_rejected_cleanly(
    monkeypatch,
    tmp_path,
) -> None:
    # An absolute signed URL that redirects to a non-http(s) target must be
    # rejected against the same allowlist that admitted the initial URL — an
    # automatic follow would raise requests.InvalidSchema, whose message echoes
    # the redirect URL (which can carry signed credentials).
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"
    redirect_target = "file:///etc/passwd"
    external_get_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append((url, kwargs))
        return FakeDataResponse(b"", status_code=302, headers={"Location": redirect_target})

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "Artifact artifact-model download failed" in message
    assert "unsupported target" in message
    assert redirect_target not in message
    assert "Traceback" not in message
    # Only the initial signed URL was fetched; the disallowed target never was.
    assert [url for url, _ in external_get_calls] == ["https://storage.example/artifact-model"]


def test_download_signed_url_follows_http_redirect_with_recheck(
    monkeypatch,
    tmp_path,
) -> None:
    # An http(s) → http(s) signed-URL redirect is followed, but manually: every
    # hop is fetched with allow_redirects=False, unauthenticated, and rechecked
    # against the http(s) allowlist.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"
    responses = [
        FakeDataResponse(
            b"", status_code=302, headers={"Location": "https://cdn.example/blob"}
        ),
        FakeDataResponse(b"redirected bytes"),
    ]
    external_get_calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append((url, kwargs))
        return responses[len(external_get_calls) - 1]

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"redirected bytes"
    expected_kwargs = {"timeout": 60, "stream": True, "allow_redirects": False}
    assert external_get_calls == [
        ("https://storage.example/artifact-model", expected_kwargs),
        ("https://cdn.example/blob", expected_kwargs),
    ]
    # The redirect response was closed once its Location was consumed.
    assert responses[0].closed


def test_download_signed_url_redirect_loop_surfaces_clean_error(
    monkeypatch,
    tmp_path,
) -> None:
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        return FakeDataResponse(b"", status_code=302, headers={"Location": url})

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "exceeded" in message
    assert "redirects" in message
    assert "Traceback" not in message


# ---------------------------------------------------------------------------
# Internal-host guard (SSRF)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signed_url",
    [
        "http://127.0.0.1/artifact-model",
        "http://127.9.8.7:9000/artifact-model",
        "HTTP://LOCALHOST/artifact-model",
        "http://internal.localhost/artifact-model",
        "http://[::1]/artifact-model",
        "http://[::ffff:127.0.0.1]/artifact-model",
        "http://169.254.169.254/latest/meta-data",
        "http://[fe80::1]/artifact-model",
        "http://10.0.0.5/artifact-model",
        "http://172.16.0.5:8443/artifact-model",
        "http://192.168.1.5/artifact-model",
        "http://100.64.0.1/artifact-model",
        "http://100.127.255.254/artifact-model",
        "http://[fd00::5]/artifact-model",
        "http://0.0.0.0/artifact-model",
        "http://127.0.0.1./artifact-model",
        "http://localhost./artifact-model",
        "http://[64:ff9b::a9fe:a9fe]/artifact-model",
        "http://[64:ff9b::7f00:1]/artifact-model",
        "http://[64:ff9b:1::7f00:1]/artifact-model",
    ],
)
def test_download_rejects_internal_signed_url_host_by_default(
    signed_url: str,
    monkeypatch,
    tmp_path,
) -> None:
    # A signed URL aimed at an internal host (loopback, link-local/metadata,
    # RFC1918, CGNAT, unique-local, unspecified) must be rejected before any fetch —
    # a hostile signed URL must not turn the CLI into a local-network SSRF
    # client. The message names the host and the override, never the full URL.
    monkeypatch.delenv("TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS", raising=False)
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = signed_url
    external_get_calls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append(url)
        return FakeDataResponse(b"must not be fetched")

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "Artifact artifact-model download failed" in message
    assert "is an internal address" in message
    assert "TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS" in message
    assert "Traceback" not in message
    assert signed_url not in message
    assert external_get_calls == []


def test_download_allows_internal_signed_url_host_with_override(
    monkeypatch,
    tmp_path,
) -> None:
    # Local/dev/kind stands legitimately serve loopback signed URLs; the env
    # override restores that behavior, including for redirect hops.
    monkeypatch.setenv("TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS", "1")
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "http://127.0.0.1:9000/artifact-model"
    responses = [
        FakeDataResponse(
            b"", status_code=302, headers={"Location": "http://127.0.0.1:9001/blob"}
        ),
        FakeDataResponse(b"loopback bytes"),
    ]
    external_get_calls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append(url)
        return responses[len(external_get_calls) - 1]

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"loopback bytes"
    assert external_get_calls == [
        "http://127.0.0.1:9000/artifact-model",
        "http://127.0.0.1:9001/blob",
    ]


def test_download_rejects_signed_url_redirect_to_internal_host(
    monkeypatch,
    tmp_path,
) -> None:
    # An external signed URL must not be able to hop to an internal host via a
    # redirect: each hop is rechecked, and the internal target is never fetched.
    monkeypatch.delenv("TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS", raising=False)
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"
    external_get_calls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append(url)
        return FakeDataResponse(
            b"", status_code=302, headers={"Location": "http://169.254.169.254/latest"}
        )

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "signed URL redirected to host" in message
    assert "is an internal address" in message
    assert "Traceback" not in message
    assert external_get_calls == ["https://storage.example/artifact-model"]


@pytest.mark.parametrize(
    "signed_url",
    [
        "http://2130706433/artifact-model",
        "http://0177.0.0.1/artifact-model",
        "http://0x7f000001/artifact-model",
        "HTTP://0X7F000001/artifact-model",
        "http://0x7f.0.0.1/artifact-model",
        "http://017700000001/artifact-model",
        "http://127.1/artifact-model",
        "http://0/artifact-model",
        "http://0x0/artifact-model",
        "http://134744072/artifact-model",
    ],
)
def test_download_rejects_non_canonical_numeric_signed_url_host(
    signed_url: str,
    monkeypatch,
    tmp_path,
) -> None:
    # The socket layer's inet_aton semantics accept decimal/octal/hex and
    # dotted short-form IPv4 spellings that ipaddress rejects, so these could
    # smuggle a loopback/metadata address past a canonical-literal check. Any
    # non-canonical numeric host is rejected outright — including spellings of
    # external addresses (134744072 is 8.8.8.8): no legitimate signed URL
    # spells its host that way.
    monkeypatch.delenv("TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS", raising=False)
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = signed_url
    external_get_calls: list[str] = []

    def fake_get(url: str, **kwargs: Any) -> FakeDataResponse:
        external_get_calls.append(url)
        return FakeDataResponse(b"must not be fetched")

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fake_get)

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "is a non-canonical numeric address" in message
    assert "TANGLE_ALLOW_INTERNAL_ARTIFACT_HOSTS" in message
    assert "Traceback" not in message
    assert external_get_calls == []


@pytest.mark.parametrize(
    "host",
    [
        "8.8.8.8",
        "storage.example",
        "storage.example.",
        "100.63.255.255",  # just below the CGNAT range
        "100.128.0.0",  # just above the CGNAT range
        "2600::1",
        "64:ff9b::808:808",  # NAT64-embedded 8.8.8.8 is external
        "::ffff:0x7f000001",  # not parseable by the socket layer either
        "",
        None,
    ],
)
def test_host_rejection_reason_allows_external_hosts(host: str | None) -> None:
    assert tangle_artifacts._host_rejection_reason(host) is None


# ---------------------------------------------------------------------------
# Download hardening: streaming, safe writes, collision/overwrite/symlink
# ---------------------------------------------------------------------------


class _DiskFullHandle:
    """Real artifact file handle whose writes fail like a full disk."""

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    def write(self, data: bytes) -> int:
        raise OSError(28, "No space left on device")

    def close(self) -> None:
        self._handle.close()


def test_download_mid_stream_write_failure_surfaces_clean_error(
    monkeypatch,
    tmp_path,
) -> None:
    # A local write failure mid-download (e.g. disk full) must surface as a
    # concise RuntimeError — the CLI only translates RuntimeError into a clean
    # error, so a raw OSError would escape as a traceback — and the partial
    # file must be cleaned up.
    client = _single_root_artifact_client()
    client.data_responses["artifact-model"] = b"artifact bytes"
    real_open = tangle_artifacts._open_new_artifact_file
    monkeypatch.setattr(
        tangle_artifacts,
        "_open_new_artifact_file",
        lambda dest: _DiskFullHandle(real_open(dest)),
    )

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "Cannot write artifact to" in message
    assert "No space left on device" in message
    assert "Traceback" not in message
    # The partially-created file was removed.
    assert list(tmp_path.iterdir()) == []


def test_download_streams_to_disk_without_buffering_full_body(tmp_path) -> None:
    client = _single_root_artifact_client()
    client.stream_responses["artifact-model"] = StreamingOnlyResponse(
        [b"chunk-a", b"chunk-b", b"chunk-c"]
    )

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    # The body is reassembled from streamed chunks; the production path never
    # touches ``response.content`` (which would raise here).
    assert artifacts["root::model"].read_bytes() == b"chunk-achunk-bchunk-c"
    assert client.stream_responses["artifact-model"].iter_calls  # streaming was used


class _FailingStreamResponse:
    """200 response whose body read fails mid-stream like a dropped connection."""

    def __init__(self, message: str = "connection reset") -> None:
        self.status_code = 200
        self.closed = False
        self._message = message

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int = 1) -> Any:
        yield b"partial"
        raise requests.ConnectionError(self._message)

    def close(self) -> None:
        self.closed = True


# Fake signed-URL credential embedded in transport-error messages below. Real
# ``requests`` transport errors echo the request URL (``Max retries exceeded
# with url: ...``), so signed-URL failure paths must not include ``str(exc)``.
_SIGNED_URL_SECRET = "X-Signature=super-secret-signature"


def test_download_direct_stream_transport_error_is_clean(tmp_path) -> None:
    client = _single_root_artifact_client()
    client.stream_responses["artifact-model"] = _FailingStreamResponse()

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "streaming failed" in message
    assert "Traceback" not in message
    # The partial file is cleaned up.
    assert list(tmp_path.iterdir()) == []


def test_download_signed_url_stream_transport_error_is_clean(tmp_path, monkeypatch) -> None:
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"
    monkeypatch.setattr(
        "tangle_cli.artifacts.requests.get",
        lambda *a, **k: _FailingStreamResponse(
            f"Connection broken for url: https://storage.example/artifact-model?{_SIGNED_URL_SECRET}"
        ),
    )

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "signed-URL download streaming failed: ConnectionError" in message
    assert _SIGNED_URL_SECRET not in message
    assert "Traceback" not in message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "exc", [requests.ConnectionError("dns failure"), requests.Timeout("timed out")]
)
def test_download_direct_data_pre_response_transport_error_is_clean(
    exc: Exception, tmp_path
) -> None:
    # The initial direct /data request fails before returning a response
    # (DNS/TLS/timeout/connection). The raw requests exception must not escape.
    client = _single_root_artifact_client()

    def make_request(method: str, path: str, **kwargs: Any) -> Any:
        raise exc

    client.request_raw = make_request  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "GET /api/artifacts/artifact-model/data transport failed" in message
    assert "Traceback" not in message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "exc, detail",
    [
        (
            requests.ConnectionError(
                f"Max retries exceeded with url: /artifact-model?{_SIGNED_URL_SECRET}"
            ),
            "ConnectionError",
        ),
        (requests.Timeout(f"timed out for url: /artifact-model?{_SIGNED_URL_SECRET}"), "Timeout"),
    ],
)
def test_download_absolute_signed_url_pre_response_transport_error_is_clean(
    exc: Exception, detail: str, tmp_path, monkeypatch
) -> None:
    # Absolute signed URL fetch fails before any response object exists (DNS,
    # TLS, timeout). The raw requests exception must not escape the CLI's
    # RuntimeError handler, and its message (which echoes the request URL,
    # including signed credentials) must not be included.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "https://storage.example/artifact-model"

    def fail_get(*args: Any, **kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr("tangle_cli.artifacts.requests.get", fail_get)

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert f"signed-URL transport failed: {detail}" in message
    assert "GET /api/artifacts/artifact-model/data returned HTTP 404" in message
    assert _SIGNED_URL_SECRET not in message
    assert "Traceback" not in message
    assert list(tmp_path.iterdir()) == []


def test_download_relative_signed_url_pre_response_transport_error_is_clean(tmp_path) -> None:
    # Relative signed path fetch via the client raises before a response.
    client = _single_root_artifact_client()
    client.data_status_codes["artifact-model"] = 404
    client.signed_urls["artifact-model"] = "/api/artifacts/artifact-model/signed-data"

    original_make_request = client.request_raw

    def make_request(method: str, path: str, **kwargs: Any) -> Any:
        if path.endswith("/signed-data"):
            raise requests.ConnectionError(
                f"connection refused for url: {path}?{_SIGNED_URL_SECRET}"
            )
        return original_make_request(method, path, **kwargs)

    client.request_raw = make_request  # type: ignore[method-assign]

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    message = str(exc_info.value)
    assert "signed-URL transport failed: ConnectionError" in message
    assert _SIGNED_URL_SECRET not in message
    assert "Traceback" not in message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "artifact_id, encoded",
    [
        ("a/b", "a%2Fb"),
        ("../secret", "..%2Fsecret"),
        ("a?b#c", "a%3Fb%23c"),
        ("with space", "with%20space"),
    ],
)
def test_download_percent_encodes_artifact_id_path_segment(
    artifact_id: str, encoded: str, tmp_path
) -> None:
    # A hostile/unusual artifact id must be percent-encoded as a single path
    # segment (matching the generated client) so it cannot alter the request
    # path structure or inject query/fragment components.
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {}, output_artifacts={"out": {"id": artifact_id}}
    )

    _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    expected = f"/api/artifacts/{encoded}/data"
    assert ("GET", expected) in client.make_request_calls
    # The raw id is never interpolated unescaped, and there is exactly one
    # artifacts path prefix (no extra segments / query / fragment injected).
    requested = [path for _, path in client.make_request_calls]
    assert f"/api/artifacts/{artifact_id}/data" not in requested
    assert all(path.count("/api/artifacts/") == 1 for path in requested)
    assert all("?" not in path and "#" not in path for path in requested)


def test_download_relative_signed_url_is_not_double_encoded(tmp_path) -> None:
    # A backend-supplied relative signed URL is passed to the client verbatim;
    # any percent-escapes it already contains must not be re-encoded.
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {}, output_artifacts={"model": {"id": "artifact-model"}}
    )
    client.data_status_codes["artifact-model"] = 404
    # An already-escaped relative signed path (note the literal %2F).
    signed_path = "/api/artifacts/artifact%2Fmodel/signed-data"
    client.signed_urls["artifact-model"] = signed_path
    client.data_responses["artifact%2Fmodel"] = b"signed bytes"

    artifacts = _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)

    assert artifacts["root::model"].read_bytes() == b"signed bytes"
    assert ("GET", signed_path) in client.make_request_calls
    # The pre-existing %2F must not become %252F.
    assert "%252F" not in "".join(p for _, p in client.make_request_calls)


def test_download_out_dir_is_existing_file_is_clean_error(tmp_path) -> None:
    # --out-dir pointing at an existing file must surface a concise error, not a
    # raw FileExistsError/NotADirectoryError traceback.
    client = _single_root_artifact_client()
    client.data_responses = {"artifact-model": b"bytes"}
    out_file = tmp_path / "not-a-dir"
    out_file.write_text("i am a file", encoding="utf-8")

    with pytest.raises(RuntimeError) as exc_info:
        _manager(client).download_result_artifacts("root-exec", out_dir=out_file)
    message = str(exc_info.value)
    assert "Cannot create output directory" in message
    assert "Traceback" not in message
    # No download was attempted.
    assert client.make_request_calls == []


def test_download_refuses_to_overwrite_existing_file(tmp_path) -> None:
    client = _single_root_artifact_client()
    client.data_responses = {"artifact-model": b"new bytes"}
    dest = tmp_path / "root__model__artifact-mod"
    dest.write_bytes(b"original")

    with pytest.raises(RuntimeError, match="Refusing to overwrite existing path"):
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    # The pre-existing file is left untouched.
    assert dest.read_bytes() == b"original"


def test_download_refuses_directory_target(tmp_path) -> None:
    client = _single_root_artifact_client()
    client.data_responses = {"artifact-model": b"new bytes"}
    (tmp_path / "root__model__artifact-mod").mkdir()

    with pytest.raises(RuntimeError, match="Refusing to overwrite existing path"):
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)


def test_download_refuses_symlink_target_without_following(tmp_path) -> None:
    client = _single_root_artifact_client()
    client.data_responses = {"artifact-model": b"new bytes"}
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    link = tmp_path / "root__model__artifact-mod"
    link.symlink_to(outside)

    with pytest.raises(RuntimeError, match="Refusing to overwrite existing path"):
        _manager(client).download_result_artifacts("root-exec", out_dir=tmp_path)
    # The symlink target must not be written through.
    assert outside.read_bytes() == b"secret"


def test_download_collision_between_distinct_artifacts_is_refused(tmp_path) -> None:
    # Two distinct artifact ids that sanitize/truncate to the same filename
    # (same owner+output+12-char prefix) must not silently overwrite each other.
    client = FakeArtifactClient()
    child = _execution(
        {},
        output_artifacts={"out": {"id": "abcdefghijkl-second"}},
    )
    client.execution_details["root-exec"] = _execution(
        {},
        child_executions={"root": child},
        output_artifacts={"out": {"id": "abcdefghijkl-first"}},
    )
    client.data_responses = {
        "abcdefghijkl-first": b"first",
        "abcdefghijkl-second": b"second",
    }

    with pytest.raises(RuntimeError, match="already used by artifact"):
        _manager(client).download_result_artifacts(
            "root-exec", out_dir=tmp_path, include_children=True
        )


def test_download_sanitizes_artifact_id_and_stays_in_out_dir(tmp_path) -> None:
    # A hostile artifact id / owner containing path separators must not escape
    # the output directory; every filename segment is sanitized.
    client = FakeArtifactClient()
    client.execution_details["root-exec"] = _execution(
        {},
        child_executions={
            "../escape": _execution({}, output_artifacts={"o": {"id": "../../etc/pwn"}})
        },
        output_artifacts={},
    )
    # The direct /data request percent-encodes the id, so the fake client sees
    # the encoded segment as the lookup key.
    client.data_responses = {"..%2F..%2Fetc%2Fpwn": b"payload"}

    artifacts = _manager(client).download_result_artifacts(
        "root-exec", out_dir=tmp_path, include_children=True
    )

    (written,) = artifacts.values()
    assert written.parent == tmp_path
    assert written.read_bytes() == b"payload"
    # The request path encodes the traversal id as one segment.
    assert ("GET", "/api/artifacts/..%2F..%2Fetc%2Fpwn/data") in client.make_request_calls
    # No traversal outside the output directory.
    assert not (tmp_path.parent / "etc").exists()
