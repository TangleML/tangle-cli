import importlib
import json
import sys

import httpx
import pytest

from tangle_cli import api_cli, cli, cli_helpers, component_inspector, components_cli, published_components_cli


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
    assert "published-components" in output

    run_app(app, ["sdk", "components", "--help"])
    assert "Work with Tangle component definitions" in capsys.readouterr().out

    run_app(app, ["sdk", "published-components", "--help"])
    assert "Inspect and search published Tangle components" in capsys.readouterr().out

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


def test_sdk_published_components_commands_call_inspection_helpers(monkeypatch, capsys):
    app = cli.build_app()
    fake_client = object()
    client_calls = []

    def fake_client_from_options(**kwargs):
        client_calls.append(kwargs)
        return fake_client

    monkeypatch.setattr(
        published_components_cli,
        "LazyTangleApiClient",
        fake_client_from_options,
    )
    monkeypatch.setattr(
        component_inspector,
        "search_components",
        lambda client, **kwargs: {"client_ok": client is fake_client, "search": kwargs},
    )
    monkeypatch.setattr(
        component_inspector,
        "inspect_by_name",
        lambda client, name, **kwargs: {
            "client_ok": client is fake_client,
            "name": name,
            "inspect": kwargs,
        },
    )
    monkeypatch.setattr(
        component_inspector,
        "inspect_by_digest",
        lambda client, digest, **kwargs: {
            "client_ok": client is fake_client,
            "digest": digest,
            "inspect": kwargs,
        },
    )
    monkeypatch.setattr(
        component_inspector,
        "get_standard_library",
        lambda client: {"client_ok": client is fake_client, "folders": []},
    )

    run_app(
        app,
        [
            "sdk",
            "published-components",
            "search",
            "demo",
            "--include-deprecated",
            "--published-by",
            "user@example.com",
            "--digest",
            "sha256:abc",
            "--base-url",
            "https://api.test",
            "-H",
            "Cloud-Auth: token",
        ],
    )
    search_result = json.loads(capsys.readouterr().out)
    assert search_result["client_ok"] is True
    assert search_result["search"] == {
        "name": "demo",
        "include_deprecated": True,
        "published_by": "user@example.com",
        "digest": "sha256:abc",
    }
    assert client_calls[-1]["base_url"] == "https://api.test"
    assert client_calls[-1]["header"] == ["Cloud-Auth: token"]

    run_app(
        app,
        [
            "sdk",
            "published-components",
            "inspect",
            "demo",
            "--all-versions",
            "--include-deprecated",
            "--full-spec",
        ],
    )
    name_result = json.loads(capsys.readouterr().out)
    assert name_result["name"] == "demo"
    assert name_result["inspect"]["include_all_versions"] is True
    assert name_result["inspect"]["include_deprecated"] is True
    assert name_result["inspect"]["full_spec"] is True

    run_app(
        app,
        [
            "sdk",
            "published-components",
            "inspect",
            "--digest",
            "sha256:def",
            "--follow-deprecated",
        ],
    )
    digest_result = json.loads(capsys.readouterr().out)
    assert digest_result["digest"] == "sha256:def"
    assert digest_result["inspect"] == {
        "full_spec": False,
        "follow_deprecated": True,
    }

    run_app(app, ["sdk", "published-components", "library"])
    library_result = json.loads(capsys.readouterr().out)
    assert library_result == {"client_ok": True, "folders": []}


def test_sdk_published_components_search_uses_config_with_cli_precedence(monkeypatch, tmp_path, capsys):
    app = cli.build_app()
    config = tmp_path / "published.yaml"
    config.write_text(
        "name: from-config\n"
        "include_deprecated: true\n"
        "published_by: config@example.com\n"
        "digest: sha256:config\n"
        "base_url: https://config.example\n"
        "token: config-token\n"
        "auth_header: Bearer config-auth\n"
        "header:\n"
        "  - 'X-Config: yes'\n",
        encoding="utf-8",
    )
    fake_client = object()
    client_calls = []

    def fake_client_from_options(**kwargs):
        client_calls.append(kwargs)
        return fake_client

    monkeypatch.setattr(published_components_cli, "LazyTangleApiClient", fake_client_from_options)
    monkeypatch.setattr(
        component_inspector,
        "search_components",
        lambda client, **kwargs: {"client_ok": client is fake_client, "search": kwargs},
    )

    run_app(
        app,
        [
            "sdk",
            "published-components",
            "search",
            "from-cli",
            "--config",
            str(config),
            "--digest",
            "sha256:cli",
        ],
    )

    result = json.loads(capsys.readouterr().out)
    assert result["search"] == {
        "name": "from-cli",
        "include_deprecated": True,
        "published_by": "config@example.com",
        "digest": "sha256:cli",
    }
    assert client_calls[-1] == {
        "base_url": "https://config.example",
        "token": "config-token",
        "auth_header": "Bearer config-auth",
        "header": ["X-Config: yes"],
        "include_env_credentials": False,
        "command_name": "published-component commands",
    }


def test_sdk_published_components_inspect_and_library_use_config(monkeypatch, tmp_path, capsys):
    app = cli.build_app()
    inspect_config = tmp_path / "inspect.yaml"
    inspect_config.write_text(
        "digest: sha256:config\n"
        "follow_deprecated: true\n"
        "full_spec: true\n"
        "base_url: https://inspect.example\n",
        encoding="utf-8",
    )
    library_config = tmp_path / "library.json"
    library_config.write_text(json.dumps({"base_url": "https://library.example"}), encoding="utf-8")
    fake_client = object()
    client_calls = []

    def fake_client_from_options(**kwargs):
        client_calls.append(kwargs)
        return fake_client

    monkeypatch.setattr(published_components_cli, "LazyTangleApiClient", fake_client_from_options)
    monkeypatch.setattr(
        component_inspector,
        "inspect_by_digest",
        lambda client, digest, **kwargs: {
            "client_ok": client is fake_client,
            "digest": digest,
            "inspect": kwargs,
        },
    )
    monkeypatch.setattr(
        component_inspector,
        "get_standard_library",
        lambda client: {"client_ok": client is fake_client},
    )

    run_app(
        app,
        ["sdk", "published-components", "inspect", "--config", str(inspect_config)],
    )
    inspect_result = json.loads(capsys.readouterr().out)
    assert inspect_result["digest"] == "sha256:config"
    assert inspect_result["inspect"] == {"full_spec": True, "follow_deprecated": True}
    assert client_calls[-1]["base_url"] == "https://inspect.example"
    assert client_calls[-1]["include_env_credentials"] is False

    run_app(
        app,
        ["sdk", "published-components", "library", "--config", str(library_config)],
    )
    library_result = json.loads(capsys.readouterr().out)
    assert library_result == {"client_ok": True}
    assert client_calls[-1]["base_url"] == "https://library.example"
    assert client_calls[-1]["include_env_credentials"] is False


def test_lazy_tangle_api_client_uses_static_client():
    from tangle_cli.client import TangleApiClient

    proxy = cli_helpers.LazyTangleApiClient(
        base_url="https://api.test",
        token="token",
        auth_header="Bearer auth",
        header=["X-Test: yes"],
        include_env_credentials=False,
        command_name="published-component commands",
    )
    client = proxy._get_client()

    assert isinstance(client, TangleApiClient)
    assert client.base_url == "https://api.test"
    assert client.token == "token"
    assert client.auth_header == "Bearer auth"
    assert client.header == ["X-Test: yes"]
    assert client.include_env_credentials is False


def test_sdk_published_components_inspect_requires_name_or_digest():
    app = cli.build_app()

    with pytest.raises(SystemExit) as exc_info:
        app(["sdk", "published-components", "inspect"])
    assert str(exc_info.value) == "Provide exactly one of NAME or --digest DIGEST"

    with pytest.raises(SystemExit) as exc_info:
        app([
            "sdk",
            "published-components",
            "inspect",
            "demo",
            "--digest",
            "sha256:abc",
        ])
    assert str(exc_info.value) == "Provide exactly one of NAME or --digest DIGEST"


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


def test_api_refresh_and_reset_cache_do_not_require_official_schema(monkeypatch, tmp_path):
    def fail_load_schema():  # pragma: no cover - assertion helper
        raise FileNotFoundError("missing tangle_api.schema")

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)

    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "refresh"])
    refresh_app = api_cli.build_app()
    assert refresh_app is not None

    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "reset-cache"])
    reset_app = api_cli.build_app()
    assert reset_app is not None
    assert not api_cli._argv_requests_api_schema(api_cli.sys.argv)
    assert not api_cli._argv_dispatches_dynamic_command(api_cli.sys.argv)


def test_api_help_without_official_schema_keeps_static_commands_unregistered(monkeypatch, tmp_path, capsys):
    def fail_load_schema():
        raise FileNotFoundError("missing tangle_api.schema")

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "--help"])

    app = api_cli.build_app()
    run_app(app, ["--help"])

    output = capsys.readouterr().out
    assert "refresh" in output
    assert "reset-cache" in output
    assert "published-components" not in output


@pytest.mark.parametrize("api_tail", [["cached-extension"], ["--help"]])
def test_auto_schema_loads_default_cache_without_ambient_auth(monkeypatch, api_tail):
    for name in (
        "TANGLE_API_AUTH_HEADER",
        "TANGLE_AUTH_HEADER",
        "TANGLE_API_HEADERS",
        "TANGLE_API_TOKEN",
        "TANGLE_API_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    loaded_cache_urls = []
    official_schema = {
        "openapi": "3.1.0",
        "paths": {"/official": {"get": {"summary": "Official"}}},
        "components": {"schemas": {}},
    }
    cached_schema = {
        "openapi": "3.1.0",
        "paths": {"/cached-extension": {"get": {"summary": "Cached extension"}}},
        "components": {"schemas": {"CachedOnly": {"type": "object"}}},
    }

    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", *api_tail])
    monkeypatch.setattr(api_cli, "default_base_url", lambda: "http://localhost:8000")
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", lambda: official_schema)

    def load_cached_schema(base_url):
        loaded_cache_urls.append(base_url)
        return cached_schema

    monkeypatch.setattr(api_cli, "load_cached_schema", load_cached_schema)

    schema = api_cli._schema_for_current_invocation()

    assert loaded_cache_urls == ["http://localhost:8000"]
    assert schema is not None
    assert "/official" in schema["paths"]
    assert "/cached-extension" in schema["paths"]


def test_api_help_with_ambient_auth_does_not_probe_implicit_localhost(
    monkeypatch, tmp_path, capsys
):
    def fail_load_cached_schema(base_url):  # pragma: no cover - assertion helper
        raise AssertionError(f"must not inspect cache for implicit base URL {base_url}")

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setattr(api_cli, "load_cached_schema", fail_load_cached_schema)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "--help"])

    app = api_cli.build_app()
    run_app(app, ["--help"])

    output = capsys.readouterr().out
    assert "refresh" in output
    assert "published-components" in output


@pytest.mark.parametrize(
    ("api_tail", "app_args", "expected"),
    [
        (["published-components", "--help"], ["published-components", "--help"], "list"),
        (["published-components", "list", "--help"], ["published-components", "list", "--help"], "--name-substring"),
        (
            ["published-components", "--schema-source", "official", "--help"],
            ["published-components", "--schema-source", "official", "--help"],
            "list",
        ),
    ],
)
def test_nested_api_help_with_ambient_auth_does_not_probe_implicit_localhost(
    monkeypatch,
    tmp_path,
    capsys,
    api_tail,
    app_args,
    expected,
):
    def fail_load_cached_schema(base_url):  # pragma: no cover - assertion helper
        raise AssertionError(f"must not inspect cache for implicit base URL {base_url}")

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setattr(api_cli, "load_cached_schema", fail_load_cached_schema)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", *api_tail])

    app = api_cli.build_app()
    run_app(app, app_args)

    assert expected in capsys.readouterr().out


def test_real_auto_api_command_with_ambient_auth_uses_transport_guard(monkeypatch):
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "cached-extension"])
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", lambda: SCHEMA)

    def fail_load_cached_schema(base_url):  # pragma: no cover - assertion helper
        raise AssertionError(f"cache should not load before auth guard, got {base_url}")

    monkeypatch.setattr(api_cli, "load_cached_schema", fail_load_cached_schema)

    with pytest.raises(SystemExit, match="TANGLE_API_URL is required"):
        api_cli._schema_for_current_invocation()


@pytest.mark.parametrize(
    "api_tail",
    [
        ["--schema-source", "cache", "--help"],
        ["--schema-source", "cache", "published-components", "--help"],
        ["published-components", "--schema-source", "cache", "--help"],
    ],
)
def test_cache_help_with_ambient_auth_and_no_base_url_uses_transport_guard(
    monkeypatch,
    api_tail,
):
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", *api_tail])

    def fail_load_cached_schema(base_url):  # pragma: no cover - assertion helper
        raise AssertionError(f"cache should not load before auth guard, got {base_url}")

    monkeypatch.setattr(api_cli, "load_cached_schema", fail_load_cached_schema)

    with pytest.raises(SystemExit, match="TANGLE_API_URL is required"):
        api_cli._schema_for_current_invocation()


def test_generated_command_with_ambient_auth_still_requires_explicit_base_url(monkeypatch):
    monkeypatch.setenv("TANGLE_API_TOKEN", "secret-token")
    monkeypatch.delenv("TANGLE_API_URL", raising=False)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "pipeline-runs", "list"],
    )

    app = api_cli.build_app(SCHEMA)
    with pytest.raises(SystemExit, match="refusing to send credentials to default"):
        app(["pipeline-runs", "list"])


def test_cold_schema_bootstrap_forwards_cli_auth_flags(monkeypatch, tmp_path):
    fetched = []

    def fail_load_schema():
        raise FileNotFoundError("missing tangle_api.schema")

    def fake_load_or_fetch_schema(base_url, **kwargs):
        fetched.append({"base_url": base_url, **kwargs})
        return {
            "openapi": "3.1.0",
            "paths": {"/cached-extension": {"get": {"summary": "Cached extension"}}},
            "components": {"schemas": {}},
        }

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)
    monkeypatch.setattr(api_cli, "load_or_fetch_schema", fake_load_or_fetch_schema)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        [
            "tangle",
            "api",
            "cached-extension",
            "--base-url",
            "http://api.test",
            "--token",
            "cli-token",
            "--auth-header",
            "Basic abc",
            "-H",
            "X-Trace: 1",
            "--header",
            "X-Other: 2",
        ],
    )

    schema = api_cli._schema_for_current_invocation()

    assert schema["paths"] == {"/cached-extension": {"get": {"summary": "Cached extension"}}}
    assert fetched == [
        {
            "base_url": "http://api.test",
            "token": "cli-token",
            "auth_header": "Basic abc",
            "header": ["X-Trace: 1", "X-Other: 2"],
            "include_env_credentials": True,
        }
    ]


def test_cold_schema_bootstrap_forwards_config_auth_flags(monkeypatch, tmp_path):
    fetched = []
    config = tmp_path / "api-config.yaml"
    config.write_text(
        "\n".join(
            [
                "base_url: http://api.test",
                "token: config-token",
                "auth_header: Basic config-auth",
                "header:",
                "  - 'X-Config: yes'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    def fail_load_schema():
        raise FileNotFoundError("missing tangle_api.schema")

    def fake_load_or_fetch_schema(base_url, **kwargs):
        fetched.append({"base_url": base_url, **kwargs})
        return {
            "openapi": "3.1.0",
            "paths": {"/cached-extension": {"get": {"summary": "Cached extension"}}},
            "components": {"schemas": {}},
        }

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TANGLE_API_TOKEN", "env-token")
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Bearer env-auth")
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)
    monkeypatch.setattr(api_cli, "load_or_fetch_schema", fake_load_or_fetch_schema)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "cached-extension", "--config", str(config)],
    )

    schema = api_cli._schema_for_current_invocation()

    assert schema["paths"] == {"/cached-extension": {"get": {"summary": "Cached extension"}}}
    assert fetched == [
        {
            "base_url": "http://api.test",
            "token": "config-token",
            "auth_header": "Basic config-auth",
            "header": ["X-Config: yes"],
            "include_env_credentials": False,
        }
    ]


def test_cold_schema_bootstrap_suppresses_env_auth_for_config_base_url(monkeypatch, tmp_path):
    fetched = []
    config = tmp_path / "api-config.yaml"
    config.write_text("base_url: http://api.test\n", encoding="utf-8")

    def fail_load_schema():
        raise FileNotFoundError("missing tangle_api.schema")

    def fake_load_or_fetch_schema(base_url, **kwargs):
        fetched.append({"base_url": base_url, **kwargs})
        return {
            "openapi": "3.1.0",
            "paths": {"/cached-extension": {"get": {"summary": "Cached extension"}}},
            "components": {"schemas": {}},
        }

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TANGLE_API_TOKEN", "env-token")
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Bearer env-auth")
    monkeypatch.setenv("TANGLE_API_HEADERS", "X-Env: secret")
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)
    monkeypatch.setattr(api_cli, "load_or_fetch_schema", fake_load_or_fetch_schema)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "cached-extension", "--config", str(config)],
    )

    schema = api_cli._schema_for_current_invocation()

    assert schema["paths"] == {"/cached-extension": {"get": {"summary": "Cached extension"}}}
    assert fetched == [
        {
            "base_url": "http://api.test",
            "token": None,
            "auth_header": None,
            "header": [],
            "include_env_credentials": False,
        }
    ]


def test_official_static_command_without_schema_fails_with_actionable_error(monkeypatch, tmp_path):
    def fail_load_schema():
        raise FileNotFoundError("missing tangle_api.schema")

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "--help"],
    )

    with pytest.raises(SystemExit) as exc_info:
        api_cli.build_app()

    message = str(exc_info.value)
    assert "Official static Tangle API commands require the native tangle-api package" in message
    assert "Install tangle-cli[native]" in message
    assert "--schema-source cache" in message


def test_cache_schema_source_does_not_require_official_schema(monkeypatch, tmp_path, capsys):
    def fail_load_schema():
        raise FileNotFoundError("missing tangle_api.schema")

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )
    monkeypatch.setattr(api_cli, "load_bundled_openapi_schema", fail_load_schema)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "list", "--schema-source", "cache", "--help"],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "list", "--schema-source", "cache", "--help"])

    output = capsys.readouterr().out
    assert "--cached-only" in output


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


def test_cold_cache_static_command_dispatch_uses_bundled_schema(monkeypatch, tmp_path):
    gets = []
    requests = []

    def fake_get(url, **kwargs):
        gets.append({"method": "GET", "url": url, **kwargs})
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("backend unavailable", request=request)

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"published_components": []})

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        [
            "tangle",
            "api",
            "published-components",
            "list",
            "--name-substring",
            "scrape v2",
            "--base-url",
            "http://api.test",
        ],
    )

    app = api_cli.build_app()
    assert gets == []

    run_app(
        app,
        [
            "published-components",
            "list",
            "--name-substring",
            "scrape v2",
            "--base-url",
            "http://api.test",
        ],
    )
    assert requests[-1]["method"] == "GET"
    assert requests[-1]["url"] == "http://api.test/api/published_components/?name_substring=scrape+v2"


def test_cold_cache_api_help_shows_static_resource_groups_and_refresh(
    monkeypatch, tmp_path, capsys
):
    gets = []

    def fake_get(url, **kwargs):
        gets.append({"method": "GET", "url": url, **kwargs})
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("backend unavailable", request=request)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "--help"])

    app = api_cli.build_app()
    run_app(app, ["--help"])

    output = capsys.readouterr().out
    assert gets == []
    assert "published-components" in output
    assert "pipeline-runs" in output
    assert "refresh" in output
    assert "Unknown command" not in output


def _oasis_like_schema_with_published_component_extensions() -> dict:
    schema = json.loads(json.dumps(SCHEMA))
    schema["paths"]["/api/published_components/"]["get"] = {
        "tags": ["components"],
        "summary": "Cached drifted list published components",
        "parameters": [
            {
                "name": "cached_only",
                "in": "query",
                "schema": {"type": "string"},
            }
        ],
    }
    schema["paths"]["/api/published_components/experimental/search"] = {
        "post": {"tags": ["components"], "summary": "Search Components"}
    }
    schema["paths"]["/api/published_components/experimental/search/schema"] = {
        "get": {"tags": ["components"], "summary": "Get Component Search Schema"}
    }
    return schema


def test_no_cache_default_schema_source_shows_official_static_only(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "--help"],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "experimental-search" not in output
    assert "experimental-search-schema" not in output


def test_default_schema_source_merges_cached_backend_extensions(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "--help"],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "experimental-search" in output
    assert "experimental-search-schema" in output


def test_default_schema_source_preserves_official_operation_on_cache_collision(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "list", "--help"],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "list", "--help"])

    output = capsys.readouterr().out
    assert "--name-substring" in output
    assert "--cached-only" not in output


def test_official_schema_source_hides_cached_extensions(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "--schema-source", "official", "--help"],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "--schema-source", "official", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "experimental-search" not in output
    assert "experimental-search-schema" not in output


def test_cache_schema_source_uses_raw_cached_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "list", "--schema-source", "cache", "--help"],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "list", "--schema-source", "cache", "--help"])

    output = capsys.readouterr().out
    assert "--cached-only" in output


def test_explicit_cache_schema_source_requires_cached_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "--schema-source", "cache", "--help"],
    )

    with pytest.raises(SystemExit) as exc_info:
        api_cli.build_app()

    assert "No cached OpenAPI schema for http://api.test" in str(exc_info.value)



def test_reset_cache_deletes_existing_cached_schema(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    api_cli.write_cached_schema(SCHEMA, "http://api.test")
    path = api_cli.cache_path("http://api.test")
    assert path.exists()
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "reset-cache", "--base-url", "http://api.test"],
    )

    app = api_cli.build_app()
    run_app(app, ["reset-cache", "--base-url", "http://api.test"])

    output = capsys.readouterr().out
    assert not path.exists()
    assert "Deleted cached OpenAPI schema for http://api.test" in output
    assert str(path) in output


def test_reset_cache_reports_noop_when_cache_absent(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    path = api_cli.cache_path("http://api.test")
    assert not path.exists()
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "reset-cache", "--base-url", "http://api.test"],
    )

    app = api_cli.build_app()
    run_app(app, ["reset-cache", "--base-url", "http://api.test"])

    output = capsys.readouterr().out
    assert "No cached OpenAPI schema for http://api.test" in output
    assert str(path) in output


def test_refresh_uses_config_with_cli_precedence(monkeypatch, tmp_path, capsys):
    calls = []
    config = tmp_path / "refresh.yaml"
    config.write_text(
        "base_url: https://config.example\n"
        "token: config-token\n"
        "auth_header: Bearer config-auth\n"
        "header:\n"
        "  - 'X-Config: yes'\n",
        encoding="utf-8",
    )

    def fake_refresh_schema(base_url, token, header, auth_header, **kwargs):
        calls.append({
            "base_url": base_url,
            "token": token,
            "header": header,
            "auth_header": auth_header,
            **kwargs,
        })
        path = tmp_path / "cached.json"
        return SCHEMA, path

    monkeypatch.setattr(api_cli, "refresh_schema", fake_refresh_schema)
    app = api_cli.build_app(SCHEMA)

    run_app(
        app,
        [
            "refresh",
            "--config",
            str(config),
            "--base-url",
            "https://cli.example",
        ],
    )

    assert calls == [{
        "base_url": "https://cli.example",
        "token": "config-token",
        "header": ["X-Config: yes"],
        "auth_header": "Bearer config-auth",
        "include_env_credentials": True,
    }]
    assert "Cached OpenAPI schema for https://cli.example" in capsys.readouterr().out


def test_refresh_config_base_url_suppresses_env_credentials(monkeypatch, tmp_path):
    requests = []
    config = tmp_path / "refresh.yaml"
    config.write_text("base_url: http://config.test\n", encoding="utf-8")

    def fake_get(url, **kwargs):
        requests.append({"url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TANGLE_API_TOKEN", "env-token")
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Bearer env-auth")
    monkeypatch.setenv("TANGLE_API_HEADERS", "X-Env: secret")
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["refresh", "--config", str(config)])

    assert requests[-1]["url"] == "http://config.test/openapi.json"
    assert "Authorization" not in requests[-1]["headers"]
    assert "X-Env" not in requests[-1]["headers"]


def test_refresh_config_base_url_preserves_config_auth(monkeypatch, tmp_path):
    requests = []
    config = tmp_path / "refresh.yaml"
    config.write_text(
        "base_url: http://config.test\n"
        "token: config-token\n"
        "auth_header: Basic config-auth\n"
        "header:\n"
        "  - 'X-Config: yes'\n",
        encoding="utf-8",
    )

    def fake_get(url, **kwargs):
        requests.append({"url": url, **kwargs})
        return json_response("GET", url, SCHEMA)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("TANGLE_API_TOKEN", "env-token")
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Bearer env-auth")
    monkeypatch.setenv("TANGLE_API_HEADERS", "X-Env: secret")
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["refresh", "--config", str(config)])

    assert requests[-1]["url"] == "http://config.test/openapi.json"
    assert requests[-1]["headers"]["Authorization"] == "Basic config-auth"
    assert requests[-1]["headers"]["X-Config"] == "yes"
    assert "X-Env" not in requests[-1]["headers"]


def test_reset_cache_uses_config_base_url(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    config = tmp_path / "reset.yaml"
    config.write_text("base_url: https://config.example\n", encoding="utf-8")
    api_cli.write_cached_schema(SCHEMA, "https://config.example")
    path = api_cli.cache_path("https://config.example")
    assert path.exists()

    app = api_cli.build_app(SCHEMA)
    run_app(app, ["reset-cache", "--config", str(config)])

    output = capsys.readouterr().out
    assert not path.exists()
    assert "Deleted cached OpenAPI schema for https://config.example" in output


def test_config_can_select_cache_schema_at_build_time(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    config = tmp_path / "schema-source.yaml"
    config.write_text(
        "base_url: http://api.test\n"
        "schema_source: cache\n",
        encoding="utf-8",
    )
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )
    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        [
            "tangle",
            "api",
            "published-components",
            "list",
            "--config",
            str(config),
            "--help",
        ],
    )

    app = api_cli.build_app()
    run_app(app, ["published-components", "list", "--config", str(config), "--help"])

    output = capsys.readouterr().out
    assert "--cached-only" in output


def test_reset_cache_returns_auto_mode_to_official_only(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setenv("TANGLE_API_URL", "http://api.test")
    api_cli.write_cached_schema(
        _oasis_like_schema_with_published_component_extensions(),
        "http://api.test",
    )

    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "reset-cache"])
    reset_app = api_cli.build_app()
    run_app(reset_app, ["reset-cache"])
    capsys.readouterr()

    monkeypatch.setattr(
        api_cli.sys,
        "argv",
        ["tangle", "api", "published-components", "--help"],
    )
    help_app = api_cli.build_app()
    run_app(help_app, ["published-components", "--help"])

    output = capsys.readouterr().out
    assert "list" in output
    assert "experimental-search" not in output
    assert "experimental-search-schema" not in output

def test_refresh_remains_available_on_cold_cache(monkeypatch, tmp_path, capsys):
    def fake_get(url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.ConnectError("backend unavailable", request=request)

    monkeypatch.setenv("TANGLE_CLI_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(api_cli.httpx, "get", fake_get)
    monkeypatch.setattr(api_cli.sys, "argv", ["tangle", "api", "refresh", "--help"])

    app = api_cli.build_app()
    run_app(app, ["refresh", "--help"])

    output = capsys.readouterr().out
    assert "Fetch /openapi.json" in output
    assert "--base-url" in output


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


def test_cli_body_at_file_reference_expands_json_file(monkeypatch, tmp_path):
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    body_path = tmp_path / "body.json"
    body_path.write_text('{"name":"from-file"}', encoding="utf-8")
    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(
        app,
        [
            "pipeline-runs",
            "create",
            "--body",
            f"@{body_path}",
            "--base-url",
            "http://api.test",
        ],
    )

    assert json.loads(requests[-1]["content"].decode()) == {"name": "from-file"}


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


def test_dynamic_command_uses_config_with_cli_precedence(monkeypatch, tmp_path):
    requests = []
    config = tmp_path / "operation.yaml"
    config.write_text(
        "base_url: http://config.test\n"
        "token: config-token\n"
        "filter: active\n"
        "limit: 9\n"
        "include_stats: true\n"
        "tag:\n"
        "  - config-tag\n",
        encoding="utf-8",
    )

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(
        app,
        [
            "pipeline-runs",
            "list",
            "--config",
            str(config),
            "--limit",
            "3",
        ],
    )

    assert (
        requests[-1]["url"]
        == "http://config.test/api/pipeline_runs/?limit=3&filter=active&include_stats=True&tag=config-tag"
    )
    assert requests[-1]["headers"]["Authorization"] == "Bearer config-token"


def test_dynamic_command_required_path_and_body_can_come_from_config(monkeypatch, tmp_path):
    requests = []
    get_config = tmp_path / "get.yaml"
    get_config.write_text(
        "base_url: http://config.test\n"
        "id: run/1\n",
        encoding="utf-8",
    )
    create_config = tmp_path / "create.yaml"
    create_config.write_text(
        "base_url: http://config.test\n"
        "body:\n"
        "  name: from-config\n",
        encoding="utf-8",
    )

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["pipeline-runs", "get", "--config", str(get_config)])
    assert requests[-1]["url"] == "http://config.test/api/pipeline_runs/run%2F1"

    run_app(app, ["pipeline-runs", "create", "--config", str(create_config)])
    assert requests[-1]["url"] == "http://config.test/api/pipeline_runs/"
    assert json.loads(requests[-1]["content"].decode()) == {"name": "from-config"}


def test_config_body_at_file_reference_is_literal_and_suppresses_env_auth(monkeypatch, tmp_path):
    requests = []
    body_path = tmp_path / "body.json"
    body_path.write_text('{"name":"from-file"}', encoding="utf-8")
    config = tmp_path / "create.yaml"
    config.write_text(
        f"base_url: http://config.test\nbody: '@{body_path}'\n",
        encoding="utf-8",
    )

    def fake_request(method, url, **kwargs):
        requests.append({"method": method, "url": url, **kwargs})
        return json_response(method, url, {"ok": True})

    monkeypatch.setenv("TANGLE_API_TOKEN", "env-token")
    monkeypatch.setenv("TANGLE_API_AUTH_HEADER", "Bearer env-auth")
    monkeypatch.setenv("TANGLE_API_HEADERS", "X-Env: secret")
    monkeypatch.setattr(api_cli.httpx, "request", fake_request)
    app = api_cli.build_app(SCHEMA)

    run_app(app, ["pipeline-runs", "create", "--config", str(config)])

    assert requests[-1]["url"] == "http://config.test/api/pipeline_runs/"
    assert json.loads(requests[-1]["content"].decode()) == f"@{body_path}"
    assert "Authorization" not in requests[-1]["headers"]
    assert "X-Env" not in requests[-1]["headers"]


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
