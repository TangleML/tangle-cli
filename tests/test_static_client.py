from __future__ import annotations

import json
from typing import Any

import requests

from tangle_cli import TangleApiClient
from tangle_cli.generated.models import (
    ListPublishedComponentsResponse,
    PipelineRunResponse,
    PublishedComponentResponse,
    SecretInfoResponse,
)


def response(payload: Any = None, status_code: int = 200) -> requests.Response:
    r = requests.Response()
    r.status_code = status_code
    if payload is None:
        r._content = b""
    else:
        r._content = json.dumps(payload).encode("utf-8")
        r.headers["Content-Type"] = "application/json"
    r.request = requests.Request("GET", "https://api.test").prepare()
    return r


class FakeSession:
    def __init__(self, responses: list[requests.Response] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses = responses or []

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.responses:
            return self.responses.pop(0)
        return response({})


def test_public_static_client_import_and_generated_operation() -> None:
    session = FakeSession([
        response({"id": "run-1", "root_execution_id": "exec-1", "created_by": "alice"})
    ])
    client = TangleApiClient("https://api.test", session=session)

    run = client.pipeline_runs_get("run/1")

    assert isinstance(run, PipelineRunResponse)
    assert run["id"] == "run-1"
    assert session.calls[0]["method"] == "GET"
    assert session.calls[0]["url"] == "https://api.test/api/pipeline_runs/run%2F1"


def test_request_json_instantiates_list_response_models() -> None:
    session = FakeSession([
        response([{"id": "run-1", "root_execution_id": "exec-1", "created_by": "alice"}])
    ])
    client = TangleApiClient("https://api.test", session=session)

    runs = client._request_json("GET", "/api/pipeline_runs/", response_model=PipelineRunResponse)

    assert isinstance(runs, list)
    assert isinstance(runs[0], PipelineRunResponse)
    assert runs[0].id == "run-1"


def test_dumb_compat_wrappers_are_removed_but_semantic_helpers_remain() -> None:
    removed = [
        "get_artifact",
        "get_artifact_signed_url",
        "get_execution_graph_state",
        "get_execution_graph_state_alt",
        "get_execution_container_state",
        "get_execution_artifacts",
        "get_execution_container_log",
        "list_pipeline_runs",
        "create_pipeline_run",
        "get_pipeline_run",
        "cancel_pipeline_run",
        "list_pipeline_run_annotations",
        "set_pipeline_run_annotation",
        "delete_pipeline_run_annotation",
        "get_current_user",
        "get_component",
        "list_published_components",
        "publish_component",
        "update_published_component",
        "list_secrets",
        "create_secret",
        "update_secret",
        "delete_secret",
        "get_component_search_schema",
        "search_components_v2",
    ]
    for name in removed:
        assert not hasattr(TangleApiClient, name)

    retained = [
        "resolve_digest",
        "get_run_details",
        "get_run_pipeline_spec",
        "get_execution_details",
        "_enrich_execution_tree",
        "find_existing_components",
        "list_published_component_infos",
        "get_component_spec",
        "stream_execution_container_log",
    ]
    for name in retained:
        assert hasattr(TangleApiClient, name)


def test_get_run_details_uses_native_operations_for_retained_semantic_helper() -> None:
    session = FakeSession([
        response({"id": "run-1", "root_execution_id": "exec-1", "created_by": "alice"}),
        response({
            "id": "exec-1",
            "pipeline_run_id": "run-1",
            "task_spec": {},
            "input_artifacts": {},
            "output_artifacts": {},
        }),
        response({"owner": "alice"}),
        response({"child_execution_status_stats": {"exec-1": {"SUCCEEDED": 1}}}),
    ])
    client = TangleApiClient("https://api.test", session=session)

    details = client.get_run_details(
        "run-1",
        include_annotations=True,
        include_execution_state=True,
    )

    assert details.run.id == "run-1"
    assert details.execution is not None
    assert details.execution.id == "exec-1"
    assert details.annotations == {"owner": "alice"}
    assert details.execution_state is not None
    assert details.execution_state.status_totals == {"SUCCEEDED": 1}
    assert [call["url"] for call in session.calls] == [
        "https://api.test/api/pipeline_runs/run-1",
        "https://api.test/api/executions/exec-1/details",
        "https://api.test/api/pipeline_runs/run-1/annotations/",
        "https://api.test/api/executions/exec-1/graph_execution_state",
    ]


def test_secret_native_operation_uses_static_generated_endpoint_shape() -> None:
    session = FakeSession([
        response({
            "secret_name": "demo",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "description": "d",
        })
    ])
    client = TangleApiClient("https://api.test", session=session)

    secret = client.secrets_create("demo", "value", description="d")

    assert isinstance(secret, SecretInfoResponse)
    assert secret.secret_name == "demo"
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == "https://api.test/api/secrets/"
    assert session.calls[0]["params"] == {"secret_name": "demo", "description": "d"}
    assert session.calls[0]["json"] == {"secret_value": "value"}


class ResolveDigestClient(TangleApiClient):
    def __init__(
        self,
        by_digest: dict[str, list[Any]] | None = None,
        by_name: dict[str, list[Any]] | None = None,
    ) -> None:
        super().__init__("https://api.test")
        self.by_digest = by_digest or {}
        self.by_name = by_name or {}
        self.lookups: list[dict[str, Any]] = []

    def published_components_list(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
    ) -> ListPublishedComponentsResponse:
        self.lookups.append({
            "include_deprecated": include_deprecated,
            "name_substring": name_substring,
            "published_by_substring": published_by_substring,
            "digest": digest,
        })
        if digest is not None:
            rows = self.by_digest.get(digest, [])
        elif name_substring is not None:
            rows = self.by_name.get(name_substring, [])
        else:
            rows = []
        return ListPublishedComponentsResponse.from_dict({"published_components": rows})


def test_resolve_digest_returns_non_deprecated_digest() -> None:
    component = PublishedComponentResponse.from_dict({
        "digest": "sha256:one",
        "published_by": "alice@example.com",
        "deprecated": False,
    })
    client = ResolveDigestClient(by_digest={"sha256:one": [component]})

    assert client.resolve_digest("sha256:one") == "sha256:one"
    assert client.lookups == [{
        "include_deprecated": True,
        "name_substring": None,
        "published_by_substring": None,
        "digest": "sha256:one",
    }]


def test_resolve_digest_follows_deprecation_successor_chain() -> None:
    client = ResolveDigestClient(
        by_digest={
            "sha256:old": [{
                "digest": "sha256:old",
                "deprecated": True,
                "superseded_by": "sha256:mid",
            }],
            "sha256:mid": [{
                "digest": "sha256:mid",
                "deprecated": True,
                "superseded_by": "new-component",
            }],
        },
        by_name={
            "new-component": [{
                "digest": "sha256:new",
                "deprecated": False,
            }],
        },
    )

    assert client.resolve_digest("sha256:old") == "sha256:new"


def test_resolve_digest_protects_against_successor_cycles() -> None:
    client = ResolveDigestClient(
        by_digest={
            "sha256:old": [{
                "digest": "sha256:old",
                "deprecated": True,
                "superseded_by": "sha256:next",
            }],
            "sha256:next": [{
                "digest": "sha256:next",
                "deprecated": True,
                "superseded_by": "sha256:old",
            }],
        },
    )

    assert client.resolve_digest("sha256:old") == "sha256:old"


def test_resolve_digest_returns_original_for_no_matches() -> None:
    client = ResolveDigestClient()

    assert client.resolve_digest("missing") == "missing"


def test_resolve_digest_returns_original_for_ambiguous_matches() -> None:
    client = ResolveDigestClient(by_digest={
        "ambiguous": [
            {"digest": "sha256:one"},
            {"digest": "sha256:two"},
        ],
    })

    assert client.resolve_digest("ambiguous") == "ambiguous"


def test_resolve_digest_falls_back_to_name_substring() -> None:
    client = ResolveDigestClient(
        by_name={"component-name": [{"digest": "sha256:by-name", "deprecated": False}]},
    )

    assert client.resolve_digest("component-name") == "sha256:by-name"
    assert client.lookups == [
        {
            "include_deprecated": True,
            "name_substring": None,
            "published_by_substring": None,
            "digest": "component-name",
        },
        {
            "include_deprecated": True,
            "name_substring": "component-name",
            "published_by_substring": None,
            "digest": None,
        },
    ]


def test_make_request_retries_after_refresh_auth_on_401() -> None:
    class RefreshingClient(TangleApiClient):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.refreshes = 0

        def _refresh_auth(self) -> None:
            self.refreshes += 1
            self.headers["Authorization"] = f"Bearer refreshed-{self.refreshes}"

    session = FakeSession([response({"error": "unauthorized"}, 401), response({"ok": True})])
    client = RefreshingClient("https://api.test", session=session)

    r = client._make_request("GET", "/api/users/me")

    assert r.status_code == 200
    assert client.refreshes == 2
    assert len(session.calls) == 2
    assert session.calls[-1]["headers"]["Authorization"] == "Bearer refreshed-2"
