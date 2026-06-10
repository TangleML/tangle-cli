import importlib
import json
import sys

import httpx
import pytest

from tangle_cli import api_cli, cli, components_cli


SCHEMA = {
    "openapi": "3.1.0",
    "paths": {
        "/api/pipeline_runs/": {
            "get": {
                "tags": ["pipelineRuns"],
                "summary": "List pipeline runs",
                "parameters": [
                    {
                        "name": "limit",
                        "in": "query",
                        "schema": {"type": "integer", "default": 20},
                    },
                    {
                        "name": "filter",
                        "in": "query",
                        "schema": {"type": "string"},
                    },
                    {
                        "name": "include_stats",
                        "in": "query",
                        "schema": {"type": "boolean"},
                    },
                    {
                        "name": "tag",
                        "in": "query",
                        "schema": {"type": "array", "items": {"type": "string"}},
                    },
                ],
            },
            "post": {
                "tags": ["pipelineRuns"],
                "summary": "Create pipeline run",
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                            }
                        }
                    }
                },
            },
        },
        "/api/pipeline_runs/{id}": {
            "get": {
                "tags": ["pipelineRuns"],
                "summary": "Get pipeline run",
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
        "/api/executions/{id}/details": {
            "get": {
                "tags": ["executions"],
                "summary": "Execution details",
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
        "/api/component_libraries/{id}": {
            "get": {
                "tags": ["components"],
                "summary": "Get component library",
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
        "/api/published_components/": {
            "get": {"tags": ["components"], "summary": "List published components"},
            "post": {"tags": ["components"], "summary": "Create published component"},
        },
        "/api/component_library_pins/me/": {
            "get": {"tags": ["components"], "summary": "Get component library pins"},
            "put": {"tags": ["components"], "summary": "Set component library pins"},
        },
        "/api/users/me": {
            "delete": {"tags": ["users"], "summary": "Delete current user"},
            "get": {"tags": ["users"], "summary": "Get current user"},
        },
    },
}


def run_app(app, args):
    with pytest.raises(SystemExit) as exc_info:
        app(args)
    assert exc_info.value.code == 0


def lower_headers(headers):
    return {name.lower(): value for name, value in dict(headers).items()}


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


def test_dynamic_command_registration_from_openapi(capsys):
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["--help"])
    assert "pipeline-runs" in capsys.readouterr().out

    run_app(app, ["pipeline-runs", "--help"])
    output = capsys.readouterr().out
    assert "list" in output
    assert "create" in output
    assert "get" in output
    assert "cancel" in output

    run_app(app, ["executions", "--help"])
    assert "details" in capsys.readouterr().out

    run_app(app, ["components", "--help"])
    output = capsys.readouterr().out
    assert "get" in output

    run_app(app, ["component-libraries", "--help"])
    assert "get" in capsys.readouterr().out

    run_app(app, ["published-components", "--help"])
    output = capsys.readouterr().out
    assert "list" in output
    assert "create" in output

    run_app(app, ["component-library-pins", "--help"])
    output = capsys.readouterr().out
    assert "me" in output
    assert "put-me" in output

    run_app(app, ["users", "--help"])
    output = capsys.readouterr().out
    assert "me" in output
    assert "delete-me" in output


def test_component_family_tag_collision_routes_by_resource_path(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["components", "get", "digest-1", "--base-url", "http://api.test"])
    assert requests[-1]["url"] == "http://api.test/api/components/digest-1"

    run_app(
        app,
        ["component-libraries", "get", "library-1", "--base-url", "http://api.test"],
    )
    assert requests[-1]["url"] == "http://api.test/api/component_libraries/library-1"

    run_app(app, ["published-components", "list", "--base-url", "http://api.test"])
    assert requests[-1]["url"] == "http://api.test/api/published_components/"

    run_app(app, ["users", "me", "--base-url", "http://api.test"])
    assert requests[-1]["method"] == "GET"
    assert requests[-1]["url"] == "http://api.test/api/users/me"

    run_app(app, ["users", "delete-me", "--base-url", "http://api.test"])
    assert requests[-1]["method"] == "DELETE"
    assert requests[-1]["url"] == "http://api.test/api/users/me"


def test_root_app_exposes_api_and_sdk_groups(capsys):
    app = cli.build_app()

    run_app(app, ["--help"])
    output = capsys.readouterr().out
    assert "api" in output
    assert "sdk" in output
    assert "components" not in output

    run_app(app, ["sdk", "--help"])
    output = capsys.readouterr().out
    assert "components" in output

    run_app(app, ["sdk", "components", "--help"])
    assert "Work with Tangle component definitions" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_info:
        app(["components"])
    assert exc_info.value.code != 0


def test_sdk_component_annotation_commands_preserve_help_and_error_behavior(capsys):
    app = cli.build_app()

    run_app(app, ["sdk", "components", "annotations", "get"])
    assert "Gets annotation values" in capsys.readouterr().out

    run_app(app, ["sdk", "components", "annotations", "set"])
    assert "Sets annotation value" in capsys.readouterr().out

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "components", "annotations", "get", "foo"])
    assert exc_info.value.code == 1
    assert "Missing required argument" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "components", "annotations", "set", "foo", "key"])
    assert exc_info.value.code == 1
    assert "Missing required argument" in capsys.readouterr().err


def test_importing_cli_modules_does_not_fetch_schema(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(sys, "argv", ["tangle", "api", "components", "list"])

    import tangle_cli.cli as root_cli

    importlib.reload(api_cli)
    importlib.reload(root_cli)

    assert calls == []


def test_non_api_root_command_does_not_fetch_when_argument_value_is_api(
    monkeypatch, tmp_path
):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "sdk", "components", "annotations", "set", "foo", "api"],
    )

    api_cli.build_app()

    assert calls == []
    assert not api_cli._argv_requests_api_schema(api_cli.sys.argv)
    assert not api_cli._argv_dispatches_dynamic_command(api_cli.sys.argv)


def test_cache_miss_fetches_schema_before_dynamic_dispatch(monkeypatch, tmp_path):
    gets = []
    requests = []

    def fake_get(url, **kwargs):
        gets.append({"method": "GET", "url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"digest": "sha256:abc"})

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        [
            "tangle",
            "api",
            "components",
            "get",
            "sha256:abc",
            "--base-url",
            "http://api.test",
            "--auth-header",
            "Basic cli-auth",
            "-H",
            "Cloud-Auth: cli-value",
        ],
    )

    app = api_cli.build_app()
    schema_headers = lower_headers(gets[0]["headers"])
    assert gets[0]["url"] == "http://api.test/openapi.json"
    assert schema_headers["authorization"] == "Basic cli-auth"
    assert schema_headers["cloud-auth"] == "cli-value"
    assert gets[0]["timeout"] == api_cli.DEFAULT_TIMEOUT_SECONDS

    run_app(
        app,
        [
            "components",
            "get",
            "sha256:abc",
            "--base-url",
            "http://api.test",
            "--auth-header",
            "Basic cli-auth",
        ],
    )
    assert requests[-1]["url"] == "http://api.test/api/components/sha256%3Aabc"


def test_cold_cache_api_short_help_does_not_treat_help_as_dynamic_command(
    monkeypatch, tmp_path, capsys
):
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("backend unavailable", request=request)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "-h"])

    app = api_cli.build_app()
    run_app(app, ["-h"])

    output = capsys.readouterr().out
    assert "refresh" in output
    assert "Unknown command" not in output


def test_cache_miss_dynamic_fetch_failure_is_actionable(monkeypatch, tmp_path):
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("backend unavailable", request=request)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        [
            "tangle",
            "api",
            "components",
            "get",
            "sha256:abc",
            "--auth-header",
            "Basic secret-value",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        api_cli.build_app()

    message = str(exc_info.value)
    assert "tangle api refresh" in message
    assert "secret-value" not in message


def test_optional_query_params_parse_and_can_be_omitted(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["pipeline-runs", "list", "--base-url", "http://api.test"])
    assert requests[-1]["url"] == "http://api.test/api/pipeline_runs/"

    run_app(
        app,
        [
            "pipeline-runs",
            "list",
            "--filter",
            "active",
            "--include-stats",
            "--tag",
            "a",
            "--tag",
            "b",
            "--base-url",
            "http://api.test",
        ],
    )
    assert (
        requests[-1]["url"]
        == "http://api.test/api/pipeline_runs/?filter=active&include_stats=True&tag=a&tag=b"
    )


def test_body_json_can_satisfy_required_simple_body_fields(monkeypatch):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(
        app,
        [
            "pipeline-runs",
            "create",
            "--body",
            '{"name":"demo"}',
            "--base-url",
            "http://api.test",
        ],
    )

    assert requests[-1]["url"] == "http://api.test/api/pipeline_runs/"
    assert json.loads(requests[-1]["content"].decode()) == {"name": "demo"}


def test_dynamic_command_invocation_maps_path_query_and_body(monkeypatch, capsys):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["pipeline-runs", "list", "--limit", "3", "--base-url", "http://api.test"])
    assert requests[-1]["url"] == "http://api.test/api/pipeline_runs/?limit=3"
    assert requests[-1]["method"] == "GET"
    assert requests[-1]["timeout"] == api_cli.DEFAULT_TIMEOUT_SECONDS

    run_app(
        app,
        [
            "pipeline-runs",
            "create",
            "--name",
            "demo",
            "--base-url",
            "http://api.test",
            "--token",
            "secret",
        ],
    )
    assert requests[-1]["url"] == "http://api.test/api/pipeline_runs/"
    assert requests[-1]["method"] == "POST"
    assert requests[-1]["headers"]["Authorization"] == "Bearer secret"
    assert json.loads(requests[-1]["content"].decode()) == {"name": "demo"}

    run_app(app, ["pipeline-runs", "get", "run/1", "--base-url", "http://api.test"])
    assert requests[-1]["url"] == "http://api.test/api/pipeline_runs/run%2F1"

    assert '"ok": true' in capsys.readouterr().out


def test_refresh_http_error_does_not_echo_response_body(monkeypatch):
    def fake_get(url, **kwargs):
        response = text_response(
            url="http://api.test/openapi.json",
            method="GET",
            text="secret-token",
            status_code=401,
        )
        raise httpx.HTTPStatusError(
            "client error", request=response.request, response=response
        )

    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    app = api_cli.build_app(SCHEMA)

    with pytest.raises(SystemExit) as exc_info:
        app(["refresh", "--base-url", "http://api.test"])

    message = str(exc_info.value)
    assert "HTTP 401 Unauthorized" in message
    assert "secret-token" not in message


def test_nested_refs_are_resolved_for_simple_array_body_fields(monkeypatch):
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/api/items/": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "names": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Name"
                                            },
                                        }
                                    },
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {"schemas": {"Name": {"type": "string"}}},
    }
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(schema)

    run_app(
        app, ["items", "create", "--names", "alice", "--base-url", "http://api.test"]
    )

    assert json.loads(requests[-1]["content"].decode()) == {"names": ["alice"]}


def test_http_error_prints_body_and_exits_with_status(monkeypatch, capsys):
    def fake_request(method, url, **kwargs):
        return text_response(method, url, "not authorized", status_code=401)

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    with pytest.raises(SystemExit) as exc_info:
        app(["pipeline-runs", "list", "--base-url", "http://api.test"])

    assert exc_info.value.code == 401
    assert "not authorized" in capsys.readouterr().err


def test_network_error_message_includes_url(monkeypatch):
    def fake_request(method, url, **kwargs):
        request = httpx.Request(method, url)
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    with pytest.raises(SystemExit) as exc_info:
        app(["pipeline-runs", "list", "--base-url", "http://api.test"])

    message = str(exc_info.value)
    assert "http://api.test/api/pipeline_runs/" in message
    assert "connection refused" in message


def test_custom_headers_apply_to_schema_fetch_and_generated_requests(monkeypatch):
    gets = []
    requests = []

    def fake_get(url, **kwargs):
        gets.append({"method": "GET", "url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, SCHEMA)

    monkeypatch.setenv(
        "TANGLE_API_HEADERS",
        json.dumps({"Cloud-Auth": "env-value", "X-Api-Key": "env-key"}),
    )
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Basic env-auth")
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(api_cli.httpx, "request", fake_request)

    api_cli.fetch_schema("http://api.test")
    env_headers = lower_headers(gets[-1]["headers"])
    assert env_headers["authorization"] == "Basic env-auth"
    assert env_headers["cloud-auth"] == "env-value"

    api_cli.fetch_schema(
        "http://api.test",
        token="bearer-value",
        header=["Cloud-Auth: cli-value"],
        auth_header="Basic cli-auth",
    )
    schema_headers = lower_headers(gets[-1]["headers"])
    assert schema_headers["authorization"] == "Basic cli-auth"
    assert schema_headers["cloud-auth"] == "cli-value"
    assert schema_headers["x-api-key"] == "env-key"

    app = api_cli.build_app(SCHEMA)
    run_app(
        app,
        [
            "pipeline-runs",
            "list",
            "--base-url",
            "http://api.test",
            "--token",
            "bearer-value",
            "--auth-header",
            "Basic cli-auth",
            "--header",
            "Cloud-Auth: cli-value",
        ],
    )
    request_headers = lower_headers(requests[-1]["headers"])
    assert request_headers["authorization"] == "Basic cli-auth"
    assert request_headers["cloud-auth"] == "cli-value"
    assert request_headers["x-api-key"] == "env-key"


def test_invalid_header_errors_do_not_echo_secret():
    with pytest.raises(SystemExit) as exc_info:
        api_cli._parse_header_entries(["Cloud-Auth super-secret"], "--header")
    assert "super-secret" not in str(exc_info.value)

    with pytest.raises(SystemExit) as exc_info:
        api_cli._normalize_auth_header("Basic bad\nsecret", "--auth-header")
    assert "secret" not in str(exc_info.value)


def test_components_annotation_commands_show_help_with_no_args(capsys):
    run_app(components_cli.app, ["annotations", "get"])
    assert "Gets annotation values" in capsys.readouterr().out

    run_app(components_cli.app, ["annotations", "set"])
    assert "Sets annotation value" in capsys.readouterr().out


def test_components_annotation_commands_error_with_partial_args(capsys):
    with pytest.raises(SystemExit) as exc_info:
        components_cli.app(["annotations", "get", "foo"])
    assert exc_info.value.code == 1
    assert "Missing required argument" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc_info:
        components_cli.app(["annotations", "set", "foo", "key"])
    assert exc_info.value.code == 1
    assert "Missing required argument" in capsys.readouterr().err


def test_default_cache_dir_uses_platformdirs_and_env_override(monkeypatch, tmp_path):
    platform_cache = tmp_path / "platform-cache"
    explicit_cache = tmp_path / "explicit-cache"
    monkeypatch.delenv("TANGLE_CLI_CACHE_DIR", raising=False)
    monkeypatch.setattr(
        api_cli.platformdirs,
        "user_cache_dir",
        lambda appname, appauthor: str(platform_cache),
    )

    assert api_cli.default_cache_dir() == platform_cache / "openapi"

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(explicit_cache))
    assert api_cli.default_cache_dir() == explicit_cache


def test_schema_cache_avoids_repeated_fetch(monkeypatch, tmp_path):
    calls = []

    def fake_get(url, **kwargs):
        calls.append({"method": "GET", "url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)

    first = api_cli.load_or_fetch_schema("http://api.test")
    second = api_cli.load_or_fetch_schema("http://api.test")

    assert first == SCHEMA
    assert second == SCHEMA
    assert len(calls) == 1
