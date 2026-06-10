import importlib
import json
import sys

import httpx
import pytest

from tangle_cli import api_schema
from tangle_cli.api_client import TangleOpenApiClient


SCHEMA = {
    "openapi": "3.1.0",
    "paths": {
        "/api/components/{digest}": {
            "get": {
                "tags": ["components"],
                "summary": "Get component",
                "parameters": [
                    {
                        "name": "digest",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
        "/api/published_components/": {
            "get": {
                "tags": ["components"],
                "summary": "List published components",
                "parameters": [
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                    {
                        "name": "tag",
                        "in": "query",
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                ],
            },
            "post": {
                "tags": ["components"],
                "summary": "Create published component",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/PublishedComponentCreate"}
                        }
                    }
                },
            },
        },
        "/api/pipeline_runs/{id}/cancel": {
            "post": {
                "tags": ["pipelineRuns"],
                "summary": "Cancel pipeline run",
                "parameters": [
                    {
                        "name": "id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string"},
                    }
                ],
            }
        },
    },
    "components": {
        "schemas": {
            "PublishedComponentCreate": {
                "allOf": [
                    {
                        "type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"],
                    },
                    {
                        "type": "object",
                        "properties": {
                            "labels": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Label"},
                            }
                        },
                    },
                ]
            },
            "Label": {"type": "string"},
        }
    },
}


def json_response(method, url, payload, status_code=200, headers=None):
    return httpx.Response(
        status_code,
        json=payload,
        headers=headers or {"Content-Type": "application/json"},
        request=httpx.Request(method, url),
    )


def text_response(method, url, text, status_code=200, headers=None):
    return httpx.Response(
        status_code,
        text=text,
        headers=headers or {"Content-Type": "text/plain"},
        request=httpx.Request(method, url),
    )


def lower_headers(headers):
    return {name.lower(): value for name, value in dict(headers).items()}


def test_from_cache_loads_cached_schema_without_network(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(httpx, "get", fake_get)
    api_schema.write_cached_schema(SCHEMA, "https://api.test")

    client = TangleOpenApiClient.from_cache(base_url="https://api.test")

    assert client.operations == (
        "components.get",
        "pipeline-runs.cancel",
        "published-components.create",
        "published-components.list",
    )
    assert calls == []


def test_from_cache_or_refresh_fetches_on_miss_then_reuses_cache(monkeypatch, tmp_path):
    gets = []

    def fake_get(url, **kwargs):
        gets.append({"url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(httpx, "get", fake_get)

    first = TangleOpenApiClient.from_cache_or_refresh(
        base_url="https://api.test/", headers={"Cloud-Auth": "cloud-token"}
    )
    second = TangleOpenApiClient.from_cache_or_refresh(base_url="https://api.test")

    assert first.operations == second.operations
    assert len(gets) == 1
    assert gets[0]["url"] == "https://api.test/openapi.json"
    assert lower_headers(gets[0]["headers"])["cloud-auth"] == "cloud-token"


def test_request_call_and_dynamic_attribute_access(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"url": url})

    monkeypatch.setattr(httpx, "request", fake_request)
    client = TangleOpenApiClient.from_schema(SCHEMA, base_url="https://api.test")

    response = client.request("components.get", digest="sha256:abc")
    assert response.json() == {"url": "https://api.test/api/components/sha256%3Aabc"}

    payload = client.call("components.get", digest="sha256:def")
    assert payload == {"url": "https://api.test/api/components/sha256%3Adef"}

    payload = client.components.get(digest="sha256:ghi")
    assert payload == {"url": "https://api.test/api/components/sha256%3Aghi"}

    payload = client.published_components.list(limit=2, tag=["a", "b"])
    assert payload == {
        "url": "https://api.test/api/published_components/?limit=2&tag=a&tag=b"
    }
    assert requests[-1]["method"] == "GET"


def test_pythonic_aliases_for_hyphenated_operations(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(httpx, "request", fake_request)
    client = TangleOpenApiClient.from_schema(SCHEMA, base_url="https://api.test")

    client.call("pipeline_runs.cancel", id="run/1")
    client.pipeline_runs.cancel(id="run/2")

    assert requests[0]["url"] == "https://api.test/api/pipeline_runs/run%2F1/cancel"
    assert requests[1]["url"] == "https://api.test/api/pipeline_runs/run%2F2/cancel"


def test_path_query_body_and_nested_ref_params(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(httpx, "request", fake_request)
    client = TangleOpenApiClient.from_schema(SCHEMA, base_url="https://api.test")

    client.call("published-components.create", name="demo", labels=["stable"])

    assert requests[-1]["method"] == "POST"
    assert requests[-1]["url"] == "https://api.test/api/published_components/"
    assert json.loads(requests[-1]["content"].decode()) == {
        "name": "demo",
        "labels": ["stable"],
    }


def test_programmatic_string_body_does_not_read_at_file_reference(monkeypatch, tmp_path):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    secret_path = tmp_path / "secret.json"
    secret_path.write_text('{"token":"secret"}', encoding="utf-8")
    monkeypatch.setattr(httpx, "request", fake_request)
    client = TangleOpenApiClient.from_schema(SCHEMA, base_url="https://api.test")

    client.call("published-components.create", body=f"@{secret_path}")

    assert json.loads(requests[-1]["content"].decode()) == f"@{secret_path}"


def test_auth_header_and_env_precedence(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setenv("TANGLE_API_HEADERS", json.dumps({"Cloud-Auth": "env-cloud"}))
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Basic env-auth")
    monkeypatch.setattr(httpx, "request", fake_request)

    client = TangleOpenApiClient.from_schema(
        SCHEMA,
        base_url="https://api.test",
        token="bearer-token",
        auth_header="Basic client-auth",
        headers={"X-Client": "yes"},
    )
    client.call(
        "components.get",
        digest="abc",
        auth_header="Basic call-auth",
        headers={"Cloud-Auth": "call-cloud"},
    )

    headers = lower_headers(requests[-1]["headers"])
    assert headers["authorization"] == "Basic call-auth"
    assert headers["cloud-auth"] == "call-cloud"
    assert headers["x-client"] == "yes"


def test_status_and_network_errors_are_httpx_errors(monkeypatch):
    def fake_http_error(method, url, **kwargs):
        return text_response(method, url, "not authorized", status_code=401)

    monkeypatch.setattr(httpx, "request", fake_http_error)
    client = TangleOpenApiClient.from_schema(SCHEMA, base_url="https://api.test")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        client.call("components.get", digest="abc")
    assert exc_info.value.response.status_code == 401
    assert exc_info.value.response.text == "not authorized"

    def fake_network_error(method, url, **kwargs):
        request = httpx.Request(method, url)
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(httpx, "request", fake_network_error)
    with pytest.raises(httpx.ConnectError):
        client.request("components.get", digest="abc")


def test_no_import_time_side_effects(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(sys, "argv", ["tangle", "api", "components", "get", "abc"])

    import tangle_cli.api_client as api_client

    importlib.reload(api_client)

    assert calls == []
