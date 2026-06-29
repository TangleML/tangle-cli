from __future__ import annotations

import json
from typing import Any

import pytest
import requests

from tangle_cli.client import TangleApiClient
from tangle_cli.logger import CaptureLogger
from tangle_api.generated.models import (
    GetExecutionInfoResponse,
    GetGraphExecutionStateResponse,
    ListPublishedComponentsResponse,
    PipelineRunResponse,
    PublishedComponentResponse,
    SecretInfoResponse,
)
from tangle_cli.models import ComponentSpec


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


def test_generated_graph_state_response_extensions_work_at_runtime() -> None:
    state = GetGraphExecutionStateResponse.from_dict({
        "child_execution_status_stats": {
            "exec-1": {"SUCCEEDED": 2, "FAILED": 1},
            "exec-2": {"SYSTEM_ERROR": 1},
        }
    })

    assert state.per_execution == {
        "exec-1": {"SUCCEEDED": 2, "FAILED": 1},
        "exec-2": {"SYSTEM_ERROR": 1},
    }
    assert state.status_totals == {"SUCCEEDED": 2, "FAILED": 1, "SYSTEM_ERROR": 1}
    assert state.failed_execution_ids == ["exec-1", "exec-2"]



@pytest.mark.parametrize("value", [None, "0", "false"])
def test_static_client_does_not_log_bodies_when_verbose_false(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("TANGLE_VERBOSE", raising=False)
    else:
        monkeypatch.setenv("TANGLE_VERBOSE", value)
    logger = CaptureLogger()
    session = FakeSession([response({"token": "response-secret"})])
    client = TangleApiClient("https://api.test", session=session, logger=logger)

    client._make_request(
        "POST",
        "/api/pipeline_runs/",
        json_data={"token": "request-secret", "name": "demo"},
    )

    assert logger.get_logs() is None


def test_static_client_verbose_env_logs_redacted_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TANGLE_VERBOSE", "1")
    logger = CaptureLogger()
    session = FakeSession([response({"id": "run-1", "token": "response-secret"})])
    client = TangleApiClient(
        "https://api.test",
        session=session,
        logger=logger,
        auth_header="Bearer request-secret",
        header=["Cloud-Auth: cloud-secret", "X-Api-Key: api-secret"],
    )

    client._make_request(
        "POST",
        "/api/pipeline_runs/",
        json_data={"name": "demo", "token": "request-secret"},
    )

    logs = logger.get_logs() or ""
    assert "[tangle-api] request: POST https://api.test/api/pipeline_runs/" in logs
    assert "request body" in logs
    assert "response body" in logs
    assert "demo" in logs
    assert "run-1" in logs
    assert "request-secret" not in logs
    assert "response-secret" not in logs
    assert "cloud-secret" not in logs
    assert "api-secret" not in logs
    assert "<redacted>" in logs


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


def test_static_client_rejects_absolute_paths_before_request() -> None:
    session = FakeSession()
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(ValueError, match="must be relative"):
        client._make_request("GET", "https://attacker.example/collect")

    assert session.calls == []


def test_static_client_rejects_network_path_references_before_request() -> None:
    session = FakeSession()
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(ValueError, match="must be relative"):
        client._make_request("GET", "//attacker.example/collect")

    assert session.calls == []


def test_cross_origin_redirect_is_rejected() -> None:
    redirect = response(status_code=307)
    redirect.url = "https://api.test/api/secrets"
    redirect.headers["Location"] = "https://attacker.example/leak"
    session = FakeSession([redirect])
    client = TangleApiClient("https://api.test", session=session)

    with pytest.raises(requests.HTTPError, match="cross-origin redirect") as exc_info:
        client._make_request("POST", "/api/secrets", json_data={"secret_value": "sensitive"})

    assert exc_info.value.response is redirect
    assert session.calls[0]["allow_redirects"] is False


def test_same_origin_redirect_is_followed() -> None:
    redirect = response(status_code=307)
    redirect.url = "https://api.test/api/old"
    redirect.headers["Location"] = "/api/new"
    ok = response({"ok": True})
    ok.url = "https://api.test/api/new"
    session = FakeSession([redirect, ok])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("POST", "/api/old", json_data={"a": 1})

    assert result is ok
    assert len(session.calls) == 2
    assert session.calls[1]["method"] == "POST"
    assert session.calls[1]["url"] == "https://api.test/api/new"
    assert session.calls[1]["params"] is None
    assert session.calls[1]["json"] == {"a": 1}


def test_rate_limit_response_is_retried(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    rate_limited = response(status_code=429)
    rate_limited.headers["Retry-After"] = "0"
    ok = response({"ok": True})
    session = FakeSession([rate_limited, ok])
    client = TangleApiClient("https://api.test", session=session)

    result = client._make_request("GET", "/api/test")

    assert result is ok
    assert len(session.calls) == 2
    assert sleeps == [0.0]


def test_numeric_retry_after_is_capped(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    rate_limited = response(status_code=429)
    rate_limited.headers["Retry-After"] = "999"
    ok = response({"ok": True})
    session = FakeSession([rate_limited, ok])
    client = TangleApiClient("https://api.test", session=session)

    client._make_request("GET", "/api/test")

    assert sleeps == [client._MAX_RETRY_AFTER_SECONDS]


def test_http_date_retry_after_is_capped(monkeypatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("tangle_cli.client.time.sleep", sleeps.append)
    monkeypatch.setattr("tangle_cli.client.time.time", lambda: 0.0)
    rate_limited = response(status_code=429)
    rate_limited.headers["Retry-After"] = "Wed, 21 Oct 2037 07:28:00 GMT"
    ok = response({"ok": True})
    session = FakeSession([rate_limited, ok])
    client = TangleApiClient("https://api.test", session=session)

    client._make_request("GET", "/api/test")

    assert sleeps == [client._MAX_RETRY_AFTER_SECONDS]


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
    assert isinstance(details.execution, GetExecutionInfoResponse)
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


def test_get_run_details_falls_back_to_run_id_as_root_execution_after_404() -> None:
    not_found = response(status_code=404)
    execution_payload = {
        "id": "root-exec",
        "task_spec": {"componentRef": {"spec": {"name": "pipeline"}}},
        "child_task_execution_ids": {},
        "input_artifacts": {},
        "output_artifacts": {},
    }
    session = FakeSession([not_found, response(execution_payload)])
    client = TangleApiClient("https://api.test", session=session)

    details = client.get_run_details("root-exec")

    assert details.run.id == "root-exec"
    assert details.run.root_execution_id == "root-exec"
    assert details.execution is not None
    assert details.execution.id == "root-exec"
    assert [call["url"] for call in session.calls] == [
        "https://api.test/api/pipeline_runs/root-exec",
        "https://api.test/api/executions/root-exec/details",
    ]


def test_get_run_details_fallback_can_include_execution_state() -> None:
    not_found = response(status_code=404)
    execution_payload = {
        "id": "root-exec",
        "task_spec": {"componentRef": {"spec": {"name": "pipeline"}}},
        "child_task_execution_ids": {},
        "input_artifacts": {},
        "output_artifacts": {},
    }
    session = FakeSession([
        not_found,
        response(execution_payload),
        response({"child_execution_status_stats": {"root-exec": {"SUCCEEDED": 1}}}),
    ])
    client = TangleApiClient("https://api.test", session=session)

    details = client.get_run_details("root-exec", include_execution_state=True)

    assert details.execution_state is not None
    assert details.execution_state.status_totals == {"SUCCEEDED": 1}
    assert session.calls[-1]["url"] == "https://api.test/api/executions/root-exec/graph_execution_state"


def graph_execution_payload() -> dict[str, Any]:
    return {
        "id": "exec-parent",
        "pipeline_run_id": "run-1",
        "task_spec": {
            "componentRef": {
                "spec": {
                    "name": "root",
                    "implementation": {
                        "graph": {
                            "tasks": {
                                "child": {
                                    "componentRef": {
                                        "digest": "sha256:child",
                                        "text": "name: child\nimplementation: bulky\n",
                                        "spec": {
                                            "name": "child-placeholder",
                                            "metadata": {
                                                "annotations": {
                                                    "python_original_code": "print('x')",
                                                    "keep": "yes",
                                                }
                                            },
                                            "implementation": {
                                                "container": {"image": "placeholder"}
                                            },
                                        },
                                    }
                                }
                            }
                        }
                    },
                }
            }
        },
        "child_task_execution_ids": {"child": "exec-child"},
        "input_artifacts": {},
        "output_artifacts": {},
    }


def child_execution_payload() -> dict[str, Any]:
    return {
        "id": "exec-child",
        "pipeline_run_id": "run-1",
        "state": "SUCCEEDED",
        "task_spec": {
            "componentRef": {
                "spec": {
                    "name": "child-real",
                    "implementation": {
                        "container": {"image": "python:3.12-slim"}
                    },
                }
            }
        },
        "child_task_execution_ids": {},
        "input_artifacts": {"input": {"id": "artifact-in"}},
        "output_artifacts": {"output": {"id": "artifact-out"}},
    }


def test_get_run_details_enriches_raw_graph_tasks_and_strips_compact_output() -> None:
    session = FakeSession([
        response({"id": "run-1", "root_execution_id": "exec-parent"}),
        response(graph_execution_payload()),
        response(child_execution_payload()),
    ])
    client = TangleApiClient("https://api.test", session=session)

    details = client.get_run_details("run-1")

    execution = details.execution
    assert isinstance(execution, GetExecutionInfoResponse)
    task = execution.tasks["child"]
    raw_task = execution.raw["task_spec"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["child"]

    expected_context = {
        "execution_id": "exec-child",
        "input_artifacts": {"input": "artifact-in"},
        "output_artifacts": {"output": "artifact-out"},
        "state": "SUCCEEDED",
    }
    for key, value in expected_context.items():
        assert task.raw[key] == value
        assert raw_task[key] == value

    assert "text" not in raw_task["componentRef"]
    raw_spec = raw_task["componentRef"]["spec"]
    assert "implementation" not in raw_spec
    assert raw_spec["metadata"]["annotations"] == {"keep": "yes"}
    assert "text" not in task.raw["componentRef"]
    assert "implementation" not in task.raw["componentRef"]["spec"]
    assert execution.child_executions["child"].id == "exec-child"


def test_get_run_details_preserves_raw_graph_implementations_when_requested() -> None:
    session = FakeSession([
        response({"id": "run-1", "root_execution_id": "exec-parent"}),
        response(graph_execution_payload()),
        response(child_execution_payload()),
    ])
    client = TangleApiClient("https://api.test", session=session)

    details = client.get_run_details("run-1", include_implementations=True)

    execution = details.execution
    assert isinstance(execution, GetExecutionInfoResponse)
    raw_task = execution.raw["task_spec"]["componentRef"]["spec"]["implementation"]["graph"]["tasks"]["child"]
    raw_spec = raw_task["componentRef"]["spec"]

    assert raw_task["componentRef"]["text"] == "name: child\nimplementation: bulky\n"
    assert raw_spec["implementation"] == {"container": {"image": "python:3.12-slim"}}
    assert raw_task["execution_id"] == "exec-child"
    assert raw_task["input_artifacts"] == {"input": "artifact-in"}
    assert raw_task["output_artifacts"] == {"output": "artifact-out"}
    assert raw_task["state"] == "SUCCEEDED"
    assert execution.tasks["child"].raw["execution_id"] == "exec-child"


def test_published_component_create_omits_unset_optional_body_fields() -> None:
    session = FakeSession([
        response({
            "digest": "digest-1",
            "name": "Demo",
            "url": "https://example.test/component.yaml",
        })
    ])
    client = TangleApiClient("https://api.test", session=session)

    published = client.published_components_create(
        name="Demo",
        url="https://example.test/component.yaml",
    )

    assert isinstance(published, PublishedComponentResponse)
    assert published.name == "Demo"
    assert session.calls[0]["method"] == "POST"
    assert session.calls[0]["url"] == "https://api.test/api/published_components/"
    assert session.calls[0]["json"] == {
        "name": "Demo",
        "url": "https://example.test/component.yaml",
    }
    assert "digest" not in session.calls[0]["json"]


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


def test_find_existing_components_returns_deduped_list_with_filters() -> None:
    client = ResolveDigestClient(
        by_digest={
            "sha256:one": [{
                "digest": "sha256:one",
                "name": "demo",
                "published_by": "alice@example.com",
            }],
            "sha256:two": [{
                "digest": "sha256:two",
                "name": "by-digest",
                "published_by": "alice@example.com",
            }],
            "sha256:spec": [{
                "digest": "sha256:spec",
                "name": "spec-name",
                "published_by": "alice@example.com",
            }],
        },
        by_name={
            "demo": [
                {
                    "digest": "sha256:one",
                    "name": "demo",
                    "published_by": "alice@example.com",
                },
                {
                    "digest": "sha256:other",
                    "name": "not-demo",
                    "published_by": "alice@example.com",
                },
            ],
            "mapped-name": [{
                "digest": "sha256:mapped",
                "name": "mapped-name",
                "published_by": "alice@example.com",
            }],
            "explicit-name": [{
                "digest": "sha256:explicit",
                "name": "explicit-name",
                "published_by": "alice@example.com",
            }],
            "spec-name": [{
                "digest": "sha256:spec",
                "name": "spec-name",
                "published_by": "alice@example.com",
            }],
            "[Official] spec-name": [{
                "digest": "sha256:spec",
                "name": "[Official] spec-name",
                "published_by": "alice@example.com",
            }],
        },
    )
    logger = CaptureLogger()
    client.logger = logger

    matches = client.find_existing_components(
        [
            "demo",
            {"name": "mapped-name", "digest": "sha256:one"},
            ComponentSpec(name="spec-name", digest="sha256:spec"),
        ],
        names=["explicit-name"],
        digests=["sha256:two"],
        include_deprecated=True,
        published_by="alice@example.com",
        verbose=True,
    )

    assert {match.digest for match in matches} == {
        "sha256:one",
        "sha256:two",
        "sha256:spec",
        "sha256:mapped",
        "sha256:explicit",
    }
    assert all(match.published_by == "alice@example.com" for match in matches)
    assert {
        (
            lookup["include_deprecated"],
            lookup["published_by_substring"],
            lookup["digest"],
            lookup["name_substring"],
        )
        for lookup in client.lookups
    } == {
        (True, "alice@example.com", "sha256:one", None),
        (True, "alice@example.com", "sha256:two", None),
        (True, "alice@example.com", "sha256:spec", None),
        (True, "alice@example.com", None, "demo"),
        (True, "alice@example.com", None, "mapped-name"),
        (True, "alice@example.com", None, "explicit-name"),
        (True, "alice@example.com", None, "spec-name"),
        (True, "alice@example.com", None, "[Official] spec-name"),
    }
    assert "Found existing component" in (logger.get_logs() or "")


def test_find_existing_components_prefers_published_by_substring() -> None:
    client = ResolveDigestClient(by_name={
        "demo": [{
            "digest": "sha256:one",
            "name": "demo",
            "published_by": "bob@example.com",
        }],
    })

    matches = client.find_existing_components(
        ["demo"],
        published_by="alice@example.com",
        published_by_substring="bob@example.com",
    )

    assert [match.name for match in matches] == ["demo"]
    assert client.lookups == [{
        "include_deprecated": False,
        "name_substring": "demo",
        "published_by_substring": "bob@example.com",
        "digest": None,
    }]


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
