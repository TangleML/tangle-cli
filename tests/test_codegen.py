from __future__ import annotations

import json
from pathlib import Path

import pytest

from tangle_cli.openapi import codegen


def _schema(paths: dict | None = None) -> dict:
    return {"openapi": "3.1.0", "paths": paths or {"/services/ping": {"get": {}}}}


def _generated_files(tmp_path: Path) -> list[Path]:
    return [
        tmp_path / "generated" / "__init__.py",
        tmp_path / "generated" / "models.py",
        tmp_path / "generated" / "operations.py",
    ]


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
    backend.mkdir(parents=True)
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
    monkeypatch.setattr(codegen, "update_openapi_from_backend", fake_update_openapi_from_backend)
    monkeypatch.setattr(codegen, "generate", fake_generate)

    codegen.main([
        "--openapi",
        str(tmp_path / "openapi.json"),
        "--out",
        str(tmp_path / "generated"),
    ])

    assert calls[0][0] == "update"
    assert calls[0][1]["backend_path"] == backend
    assert calls[1][0] == "generate"
    assert calls[1][1]["operations_class_name"] == "GeneratedTangleApiOperations"
    output = capsys.readouterr().out
    assert f"Loaded OpenAPI from backend: {backend}" in output
    assert f"Wrote {tmp_path / 'openapi.json'}" in output
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
    output = capsys.readouterr().out
    assert f"Loaded OpenAPI from snapshot: {tmp_path / 'openapi.json'}" in output
    assert f"Wrote {tmp_path / 'openapi.json'}" not in output
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


def test_codegen_main_fetches_from_openapi_url_before_generating(
    monkeypatch, tmp_path, capsys
) -> None:
    calls: list[tuple[str, object]] = []

    def fake_update_openapi_from_url(openapi_url, **kwargs):
        calls.append(("update-url", {"openapi_url": openapi_url, **kwargs}))
        openapi_path = tmp_path / "openapi.json"
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
        str(tmp_path / "openapi.json"),
        "--out",
        str(tmp_path / "generated"),
        "--openapi-url",
        "https://example.com/openapi.json",
    ])

    assert calls[0] == (
        "update-url",
        {
            "openapi_url": "https://example.com/openapi.json",
            "destination": str(tmp_path / "openapi.json"),
        },
    )
    assert calls[1][0] == "generate"
    assert calls[1][1]["operations_class_name"] == "GeneratedTangleApiOperations"
    output = capsys.readouterr().out
    assert "Loaded OpenAPI from URL: https://example.com/openapi.json" in output
    assert "Generated 1 operations from 1 paths" in output


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
    assert "def things_delete(self, id: Any) -> None:" in operations
    assert "def unknown_list(self) -> Any:" in operations
