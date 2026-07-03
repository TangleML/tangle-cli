from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tangle_cli.openapi import codegen, parser


def _schema(paths: dict | None = None) -> dict:
    return {"openapi": "3.1.0", "paths": paths or {"/services/ping": {"get": {}}}}


def _generated_files(tmp_path: Path) -> list[Path]:
    return [
        tmp_path / "generated" / "__init__.py",
        tmp_path / "generated" / "models.py",
        tmp_path / "generated" / "operations.py",
    ]


def test_generate_operations_rejects_absolute_url_paths() -> None:
    with pytest.raises(ValueError, match="must be relative"):
        codegen.generate_operations(_schema({"https://attacker.example/collect": {"get": {}}}))


def test_generate_operations_rejects_network_path_references() -> None:
    with pytest.raises(ValueError, match="must be relative"):
        codegen.generate_operations(_schema({"//attacker.example/collect": {"get": {}}}))


def test_codegen_module_imports_with_default_openapi_resource_package() -> None:
    imported = importlib.import_module("tangle_cli.openapi.codegen")

    assert imported is codegen
    assert parser.DEFAULT_OPENAPI_RESOURCE_PACKAGE == "tangle_api.schema"


def test_default_openapi_snapshot_lives_in_api_package() -> None:
    assert parser.DEFAULT_OPENAPI_PATH.match(
        "*/packages/tangle-api/src/tangle_api/schema/openapi.json"
    )
    schema = parser.load_openapi_schema()
    assert "paths" in schema


def test_explicit_openapi_path_does_not_require_default_snapshot(tmp_path) -> None:
    openapi = tmp_path / "custom-openapi.json"
    openapi.write_text(json.dumps(_schema()), encoding="utf-8")

    schema = parser.load_openapi_schema(openapi)

    assert schema["paths"] == {"/services/ping": {"get": {}}}


def test_codegen_update_from_openapi_url_writes_snapshot(tmp_path) -> None:
    source = tmp_path / "official-openapi.json"
    destination = tmp_path / "openapi.json"
    source.write_text(json.dumps(_schema()), encoding="utf-8")

    written = codegen.update_openapi_from_url(
        source.as_uri(),
        destination=destination,
    )

    assert written == destination
    assert json.loads(destination.read_text(encoding="utf-8"))["paths"] == {
        "/services/ping": {"get": {}}
    }


def test_update_openapi_from_backend_imports_app_and_uses_temp_database(tmp_path) -> None:
    backend = tmp_path / "backend"
    destination = tmp_path / "openapi.json"
    backend.mkdir()
    (backend / "api_server_main.py").write_text(
        """
import os

class App:
    def openapi(self):
        return {
            "openapi": "3.1.0",
            "x-database-uri": os.environ.get("DATABASE_URI"),
            "paths": {"/api/components/{digest}": {"get": {}}},
        }

app = App()
""".strip(),
        encoding="utf-8",
    )

    written = codegen.update_openapi_from_backend(
        backend_path=backend,
        destination=destination,
    )

    schema = json.loads(written.read_text(encoding="utf-8"))
    assert schema["paths"] == {"/api/components/{digest}": {"get": {}}}
    assert schema["x-database-uri"].startswith("sqlite:///")
    assert "openapi_codegen.sqlite" in schema["x-database-uri"]


def test_codegen_main_no_args_uses_default_backend_and_prints_summary(
    monkeypatch, tmp_path, capsys
) -> None:
    backend = tmp_path / "third_party" / "tangle"
    default_snapshot = tmp_path / "packages" / "tangle-api" / "src" / "tangle_api" / "schema" / "openapi.json"
    backend.mkdir(parents=True)
    default_snapshot.parent.mkdir(parents=True)
    (backend / "api_server_main.py").write_text("app = object()\n", encoding="utf-8")
    calls: list[tuple[str, object]] = []

    def fake_update_openapi_from_backend(**kwargs):
        calls.append(("update", kwargs))
        openapi_path = Path(kwargs["destination"])
        openapi_path.write_text(json.dumps(_schema()), encoding="utf-8")
        return openapi_path

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "DEFAULT_BACKEND_PATH", backend)
    monkeypatch.setattr(codegen, "DEFAULT_OPENAPI_PATH", default_snapshot)
    monkeypatch.setattr(codegen, "update_openapi_from_backend", fake_update_openapi_from_backend)
    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--out",
        str(tmp_path / "generated"),
    ])

    assert calls[0][0] == "update"
    assert calls[0][1]["backend_path"] == backend
    assert calls[0][1]["destination"] == default_snapshot
    assert calls[1][0] == "generate"
    assert calls[1][1]["openapi_path"] == default_snapshot
    assert calls[1][1]["operations_class_name"] == "GeneratedTangleApiOperations"
    assert calls[1][1]["model_aliases"] is None
    output = capsys.readouterr().out
    assert f"Loaded OpenAPI from backend: {backend}" in output
    assert f"Wrote {default_snapshot}" in output
    assert f"Wrote {tmp_path / 'generated' / 'models.py'}" in output
    assert "Generated 1 operations from 1 paths" in output


def test_codegen_main_missing_default_backend_fails_with_guidance(
    monkeypatch, tmp_path, capsys
) -> None:
    monkeypatch.setattr(codegen, "DEFAULT_BACKEND_PATH", tmp_path / "missing" / "tangle")

    with pytest.raises(SystemExit) as exc_info:
        codegen.main(["--openapi", str(tmp_path / "openapi.json")])

    assert exc_info.value.code == 1
    assert (
        "Default backend submodule not found. Run: git submodule update --init --recursive"
        in capsys.readouterr().err
    )


def test_codegen_main_from_snapshot_is_explicit(monkeypatch, tmp_path, capsys) -> None:
    calls: list[tuple[str, object]] = []

    def fail_update(*args, **kwargs):  # pragma: no cover - assertion helper
        raise AssertionError("snapshot mode must not update openapi.json")

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "update_openapi_from_backend", fail_update)
    monkeypatch.setattr(codegen, "update_openapi_from_url", fail_update)
    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--openapi",
        str(tmp_path / "openapi.json"),
        "--out",
        str(tmp_path / "generated"),
        "--from-snapshot",
    ])

    assert calls[0][0] == "generate"
    assert calls[0][1]["operations_class_name"] == "GeneratedTangleApiOperations"
    assert calls[0][1]["model_aliases"] is None
    output = capsys.readouterr().out
    assert f"Loaded OpenAPI from snapshot: {tmp_path / 'openapi.json'}" in output
    assert f"Wrote {tmp_path / 'openapi.json'}" not in output
    assert "Generated 1 operations from 1 paths" in output


def test_codegen_main_from_default_snapshot_uses_bundled_resolution(monkeypatch, tmp_path, capsys) -> None:
    calls: list[tuple[str, object]] = []
    default_snapshot = tmp_path / "packages" / "tangle-api" / "src" / "tangle_api" / "schema" / "openapi.json"
    default_snapshot.parent.mkdir(parents=True)
    default_snapshot.write_text(json.dumps(_schema()), encoding="utf-8")

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "DEFAULT_OPENAPI_PATH", default_snapshot)
    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main(["--from-snapshot", "--out", str(tmp_path / "generated")])

    assert calls[0][0] == "generate"
    assert calls[0][1]["openapi_path"] is None
    output = capsys.readouterr().out
    assert f"Loaded OpenAPI from snapshot: {default_snapshot}" in output
    assert "Generated 1 operations from 1 paths" in output


def test_codegen_main_accepts_custom_operations_class_name(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, object]] = []

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--openapi",
        str(tmp_path / "openapi.json"),
        "--out",
        str(tmp_path / "generated"),
        "--from-snapshot",
        "--operations-class-name",
        "GeneratedTangleApiExtensions",
    ])

    assert calls[0][0] == "generate"
    assert calls[0][1]["operations_class_name"] == "GeneratedTangleApiExtensions"
    assert calls[0][1]["model_aliases"] is None


def test_codegen_main_openapi_url_writes_default_snapshot_before_generating(
    monkeypatch, tmp_path, capsys
) -> None:
    calls: list[tuple[str, object]] = []
    default_snapshot = tmp_path / "packages" / "tangle-api" / "src" / "tangle_api" / "schema" / "openapi.json"

    def fake_update_openapi_from_url(openapi_url, **kwargs):
        calls.append(("update-url", {"openapi_url": openapi_url, **kwargs}))
        openapi_path = Path(kwargs["destination"])
        openapi_path.parent.mkdir(parents=True)
        openapi_path.write_text(json.dumps(_schema()), encoding="utf-8")
        return openapi_path

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "DEFAULT_OPENAPI_PATH", default_snapshot)
    monkeypatch.setattr(codegen, "update_openapi_from_url", fake_update_openapi_from_url)
    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--out",
        str(tmp_path / "generated"),
        "--openapi-url",
        "https://example.com/openapi.json",
    ])

    assert calls[0] == (
        "update-url",
        {
            "openapi_url": "https://example.com/openapi.json",
            "destination": default_snapshot,
        },
    )
    assert calls[1][0] == "generate"
    assert calls[1][1]["openapi_path"] == default_snapshot
    assert calls[1][1]["operations_class_name"] == "GeneratedTangleApiOperations"
    assert calls[1][1]["model_aliases"] is None
    output = capsys.readouterr().out
    assert "Loaded OpenAPI from URL: https://example.com/openapi.json" in output
    assert f"Wrote {default_snapshot}" in output
    assert "Generated 1 operations from 1 paths" in output


def test_codegen_main_openapi_url_respects_explicit_openapi_destination(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[str, object]] = []
    explicit_snapshot = tmp_path / "custom-openapi.json"

    def fake_update_openapi_from_url(openapi_url, **kwargs):
        calls.append(("update-url", {"openapi_url": openapi_url, **kwargs}))
        openapi_path = Path(kwargs["destination"])
        openapi_path.write_text(json.dumps(_schema()), encoding="utf-8")
        return openapi_path

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "update_openapi_from_url", fake_update_openapi_from_url)
    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--openapi",
        str(explicit_snapshot),
        "--out",
        str(tmp_path / "generated"),
        "--openapi-url",
        "https://example.com/openapi.json",
    ])

    assert calls[0] == (
        "update-url",
        {
            "openapi_url": "https://example.com/openapi.json",
            "destination": str(explicit_snapshot),
        },
    )
    assert calls[1][0] == "generate"
    assert calls[1][1]["openapi_path"] == str(explicit_snapshot)


def test_generate_writes_support_modules_to_custom_out(tmp_path) -> None:
    openapi = tmp_path / "openapi.json"
    out = tmp_path / "custom_generated_api"
    openapi.write_text(
        json.dumps({
            "openapi": "3.1.0",
            "paths": {
                "/api/published_components/": {
                    "get": {
                        "tags": ["components"],
                        "summary": "List published components",
                        "parameters": [
                            {
                                "name": "name_substring",
                                "in": "query",
                                "schema": {"type": "string"},
                            }
                        ],
                    }
                }
            },
            "components": {"schemas": {}},
        }),
        encoding="utf-8",
    )

    codegen.generate(openapi, out)

    assert (out / "__init__.py").exists()
    assert (out / "models.py").exists()
    operations = (out / "operations.py").read_text(encoding="utf-8")
    assert "class GeneratedTangleApiOperations" in operations
    assert "def published_components_list" in operations
    assert "name_substring" in operations


def test_generate_models_adds_default_component_spec_alias() -> None:
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/api/components/{digest}": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ComponentSpecOutput"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "ComponentSpecOutput": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "title": "ComponentSpecOutput",
                }
            }
        },
    }

    models = codegen.generate_models(schema)
    operations = codegen.generate_operations(schema)

    assert "class ComponentSpec(TangleGeneratedModel):" in models
    assert "class ComponentSpecOutput(TangleGeneratedModel):" in models
    assert "_ComponentSpecGenerated" not in models
    assert "'ComponentSpec'" in models
    assert "from .models import ComponentSpec" in operations
    assert "def components_get(self, digest: Any) -> ComponentSpec:" in operations
    assert "response_model=self._response_model('ComponentSpec', ComponentSpec)" in operations


def test_component_spec_alias_operation_deserializes_raw_spec(monkeypatch, tmp_path) -> None:
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/api/components/{digest}": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ComponentSpecOutput"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "ComponentSpecOutput": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "metadata": {"type": "object"},
                    },
                }
            }
        },
    }
    openapi = tmp_path / "openapi.json"
    openapi.write_text(json.dumps(schema), encoding="utf-8")
    out = tmp_path / "aliased_component_api"
    codegen.generate(openapi, out)
    monkeypatch.syspath_prepend(str(tmp_path))
    generated_operations = importlib.import_module("aliased_component_api.operations")

    class Client(generated_operations.GeneratedTangleApiOperations):
        def _request_json(self, *args, response_model=None, **kwargs):
            return response_model.from_dict({
                "name": "Widget",
                "metadata": {"annotations": {"version": "1"}},
            })

    spec = Client().components_get("sha256:abc")

    assert spec.__class__.__name__ == "ComponentSpec"
    assert spec.name == "Widget"
    assert spec.metadata == {"annotations": {"version": "1"}}
    assert not hasattr(spec, "version")
    assert not hasattr(spec, "data")


def test_generate_models_can_disable_default_model_aliases() -> None:
    schema = {
        "openapi": "3.1.0",
        "paths": {},
        "components": {
            "schemas": {
                "ComponentSpecOutput": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            }
        },
    }

    models = codegen.generate_models(schema, model_aliases="")

    assert "class ComponentSpec(" not in models
    assert "class ComponentSpecOutput(TangleGeneratedModel):" in models


def test_generate_models_supports_custom_model_aliases() -> None:
    schema = {
        "openapi": "3.1.0",
        "paths": {
            "/api/widgets/{id}": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/WidgetOutput"}
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {
                "WidgetOutput": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                }
            }
        },
    }

    models = codegen.generate_models(schema, model_aliases=["Widget=WidgetOutput"])
    operations = codegen.generate_operations(schema, model_aliases=["Widget=WidgetOutput"])

    assert "class Widget(TangleGeneratedModel):" in models
    assert "_WidgetGenerated" not in models
    assert "from .models import Widget" in operations
    assert "-> Widget:" in operations
    assert "response_model=self._response_model('Widget', Widget)" in operations


def test_generate_models_are_plain_by_default() -> None:
    models = codegen.generate_models({
        "openapi": "3.1.0",
        "paths": {},
        "components": {
            "schemas": {
                "GetGraphExecutionStateResponse": {
                    "type": "object",
                    "properties": {
                        "child_execution_status_stats": {"type": "object"},
                    },
                }
            }
        },
    })

    assert "from tangle_api.generated.runtime import TangleGeneratedModel" in models
    assert "generated_model_extensions" not in models
    assert "class GetGraphExecutionStateResponse(TangleGeneratedModel):" in models
    assert "_GetGraphExecutionStateResponseGenerated" not in models


def test_generate_operations_request_body_schema_override_preserves_raw_body(monkeypatch, tmp_path) -> None:
    openapi = tmp_path / "openapi.json"
    out = tmp_path / "raw_body_api"
    openapi.write_text(
        json.dumps({
            "openapi": "3.1.0",
            "paths": {
                "/api/search": {
                    "post": {
                        "operationId": "search_components",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"query": {"type": "string"}},
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
                    }
                }
            },
            "components": {"schemas": {}},
        }),
        encoding="utf-8",
    )

    codegen.generate(
        openapi,
        out,
        request_body_schemas={
            "search_create": {
                "type": "object",
                "additionalProperties": True,
                "title": "SearchQuery",
            }
        },
    )

    operations = (out / "operations.py").read_text(encoding="utf-8")
    assert "def search_create(self, body: dict[str, Any] | None = None)" in operations
    assert "query:" not in operations
    assert "json_data=body" in operations

    monkeypatch.syspath_prepend(str(tmp_path))
    generated_operations = importlib.import_module("raw_body_api.operations")

    class Client(generated_operations.GeneratedTangleApiOperations):
        def __init__(self) -> None:
            self.calls = []

        def _request_json(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"ok": True}

    payload = {"predicate": {"nested": {"value": True}}, "page_token": "next"}
    client = Client()
    client.search_create(body=payload)

    assert client.calls[0][1]["json_data"] is payload


def test_generate_operations_without_request_body_override_omits_unset_optional_body_kwargs(monkeypatch, tmp_path) -> None:
    openapi = tmp_path / "openapi.json"
    out = tmp_path / "normal_body_api"
    openapi.write_text(
        json.dumps({
            "openapi": "3.1.0",
            "paths": {
                "/api/search": {
                    "post": {
                        "operationId": "search_components",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "query": {"type": "string"},
                                            "limit": {"type": "integer"},
                                        },
                                    }
                                }
                            }
                        },
                    }
                }
            },
            "components": {"schemas": {}},
        }),
        encoding="utf-8",
    )

    codegen.generate(openapi, out)

    operations = (out / "operations.py").read_text(encoding="utf-8")
    assert "def search_create(self," in operations
    assert "query: Any = None" in operations
    assert "limit: Any = None" in operations
    assert "json_data={key: value for key, value in {'limit': limit, 'query': query}.items() if value is not None}" in operations
    assert "body: dict[str, Any] | None" not in operations

    monkeypatch.syspath_prepend(str(tmp_path))
    generated_operations = importlib.import_module("normal_body_api.operations")

    class Client(generated_operations.GeneratedTangleApiOperations):
        def __init__(self) -> None:
            self.calls = []

        def _request_json(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"ok": True}

    client = Client()
    client.search_create(query="widgets")
    client.search_create()

    assert client.calls[0][1]["json_data"] == {"query": "widgets"}
    assert client.calls[1][1]["json_data"] == {}


def test_generate_operations_preserves_required_body_kwargs(monkeypatch, tmp_path) -> None:
    openapi = tmp_path / "openapi.json"
    out = tmp_path / "required_body_api"
    openapi.write_text(
        json.dumps({
            "openapi": "3.1.0",
            "paths": {
                "/api/secrets": {
                    "post": {
                        "operationId": "create_secret",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["secret_value"],
                                        "properties": {
                                            "secret_value": {"type": "string"},
                                            "description": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        },
                    }
                }
            },
            "components": {"schemas": {}},
        }),
        encoding="utf-8",
    )

    codegen.generate(openapi, out)

    operations = (out / "operations.py").read_text(encoding="utf-8")
    assert "def secrets_create(self, secret_value: Any, description: Any = None)" in operations
    assert "json_data={**{'secret_value': secret_value}, **{key: value for key, value in {'description': description}.items() if value is not None}}" in operations

    monkeypatch.syspath_prepend(str(tmp_path))
    generated_operations = importlib.import_module("required_body_api.operations")

    class Client(generated_operations.GeneratedTangleApiOperations):
        def __init__(self) -> None:
            self.calls = []

        def _request_json(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"ok": True}

    client = Client()
    client.secrets_create("secret")

    assert client.calls[0][1]["json_data"] == {"secret_value": "secret"}


def test_codegen_main_accepts_request_body_schema_file(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, object]] = []
    schema_file = tmp_path / "body-schema.json"
    schema_file.write_text(
        json.dumps({"type": "object", "additionalProperties": True, "title": "Body"}),
        encoding="utf-8",
    )

    def fake_generate(openapi_path, generated_dir, **kwargs):
        calls.append((
            "generate",
            {
                "openapi_path": openapi_path,
                "generated_dir": generated_dir,
                **kwargs,
            },
        ))
        return _schema(), _generated_files(tmp_path)

    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--openapi",
        str(tmp_path / "openapi.json"),
        "--from-snapshot",
        "--request-body-schema",
        'inline_op={"type":"object","additionalProperties":true}',
        "--request-body-schema-file",
        f"file_op={schema_file}",
    ])

    assert calls[0][1]["request_body_schemas"] == {
        "inline_op": {"type": "object", "additionalProperties": True},
        "file_op": {"type": "object", "additionalProperties": True, "title": "Body"},
    }


def test_codegen_main_rejects_invalid_request_body_schema(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        codegen.main([
            "--openapi",
            str(tmp_path / "openapi.json"),
            "--from-snapshot",
            "--request-body-schema",
            "search_components=not-json",
        ])

    assert exc_info.value.code == 2
    assert "not valid JSON" in capsys.readouterr().err


def test_generate_supports_custom_operations_class_name(tmp_path) -> None:
    openapi = tmp_path / "openapi.json"
    out = tmp_path / "custom_generated_api"
    openapi.write_text(
        json.dumps({
            "openapi": "3.1.0",
            "paths": {"/api/components/{digest}": {"get": {}}},
            "components": {"schemas": {}},
        }),
        encoding="utf-8",
    )

    codegen.generate(
        openapi,
        out,
        operations_class_name="GeneratedTangleApiExtensions",
    )

    operations = (out / "operations.py").read_text(encoding="utf-8")
    assert "class GeneratedTangleApiExtensions" in operations
    assert "if TYPE_CHECKING:" in operations
    assert "def _request_json(" in operations
    assert "__all__ = ['GeneratedTangleApiExtensions']" in operations


def test_codegen_main_rejects_invalid_operations_class_name(tmp_path, capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        codegen.main([
            "--openapi",
            str(tmp_path / "openapi.json"),
            "--operations-class-name",
            "not-valid!",
        ])

    assert exc_info.value.code == 2
    assert "Invalid generated operations class name" in capsys.readouterr().err


def test_generate_operations_uses_concrete_return_annotations() -> None:
    operations = codegen.generate_operations({
        "openapi": "3.1.0",
        "paths": {
            "/api/arrays": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/FooResponse"},
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/maps": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "additionalProperties": {"type": "string"},
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/nullable": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "anyOf": [
                                            {"$ref": "#/components/schemas/FooResponse"},
                                            {"type": "null"},
                                        ]
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/status": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {"schema": {"type": "string"}}
                            }
                        }
                    }
                }
            },
            "/api/things/{id}": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/FooResponse"}
                                }
                            }
                        }
                    }
                },
                "delete": {"responses": {"204": {"description": "deleted"}}},
            },
            "/api/unknown": {"get": {}},
        },
        "components": {
            "schemas": {
                "FooResponse": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                }
            }
        },
    })

    assert "from collections.abc import Mapping" in operations
    assert "from typing import TYPE_CHECKING, Any" in operations
    assert "class GeneratedTangleApiOperations" in operations
    assert "if TYPE_CHECKING:" in operations
    assert "path_params: Mapping[str, Any] | None = None" in operations
    assert "def _request_json(" in operations
    assert "__all__ = ['GeneratedTangleApiOperations']" in operations
    assert "from .models import FooResponse" in operations
    assert "def arrays_list(self) -> list[FooResponse]:" in operations
    assert "def maps_list(self) -> dict[str, Any]:" in operations
    assert "def nullable_list(self) -> FooResponse | None:" in operations
    assert "def status_list(self) -> str:" in operations
    assert "def things_get(self, id: Any) -> FooResponse:" in operations
    assert "response_model=self._response_model('FooResponse', FooResponse)" in operations
    assert "def things_delete(self, id: Any) -> None:" in operations
    assert "def unknown_list(self) -> Any:" in operations
