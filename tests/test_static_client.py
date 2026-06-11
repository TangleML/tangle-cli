from __future__ import annotations

import json
from typing import Any

import requests

from tangle_cli import TangleApiClient
from tangle_cli.generated.models import PipelineRunResponse, PublishedComponentResponse
from tangle_cli.models import PipelineRun, SecretInfo


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


def test_compat_get_pipeline_run_returns_dataclass_with_dict_helpers() -> None:
    session = FakeSession([
        response({"id": "run-1", "root_execution_id": "exec-1", "created_by": "alice"})
    ])
    client = TangleApiClient("https://api.test", session=session)

    run = client.get_pipeline_run("run-1")

    assert isinstance(run, PipelineRun)
    assert run.id == "run-1"
    assert run.get("created_by") == "alice"
    assert run["root_execution_id"] == "exec-1"


def test_compat_wrapper_migration_hints_are_machine_readable() -> None:
    assert TangleApiClient.get_execution_container_state.__tangle_migrate_to__ == (
        "executions_container_state"
    )
    assert TangleApiClient.get_pipeline_run.__tangle_migrate_to__ == "pipeline_runs_get"
    assert TangleApiClient.list_published_components.__tangle_migrate_to__ == (
        "published_components_list"
    )
    assert TangleApiClient.create_secret.__tangle_migrate_to__ == "secrets_create"

    assert not hasattr(TangleApiClient.resolve_digest, "__tangle_migrate_to__")
    assert not hasattr(TangleApiClient.get_run_details, "__tangle_migrate_to__")
    assert not hasattr(TangleApiClient.find_existing_components, "__tangle_migrate_to__")
    assert not hasattr(TangleApiClient._enrich_execution_tree, "__tangle_migrate_to__")


def test_secret_helpers_use_static_generated_endpoint_shapes() -> None:
    session = FakeSession([
        response({
            "secret_name": "demo",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "description": "d",
        })
    ])
    client = TangleApiClient("https://api.test", session=session)

    secret = client.create_secret("demo", "value", description="d")

    assert isinstance(secret, SecretInfo)
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

    def list_published_components(
        self,
        include_deprecated: bool = False,
        name_substring: str | None = None,
        published_by_substring: str | None = None,
        digest: str | None = None,
    ) -> list[Any]:
        self.lookups.append({
            "include_deprecated": include_deprecated,
            "name_substring": name_substring,
            "published_by_substring": published_by_substring,
            "digest": digest,
        })
        if digest is not None:
            return self.by_digest.get(digest, [])
        if name_substring is not None:
            return self.by_name.get(name_substring, [])
        return []


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
