from __future__ import annotations

import json
from typing import Any

import requests

from tangle_cli import TangleApiClient
from tangle_cli.generated.models import PipelineRunResponse
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
