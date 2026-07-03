"""Offline OpenAPI loading helpers used by generated-client codegen.

The runtime client does not import this module. It exists so expanding the
checked-in generated client is a deterministic local operation over the
checked-in API-package ``openapi.json`` snapshot.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from tangle_cli.api_schema import OperationCommand, operation_commands

_REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OPENAPI_PATH = _REPO_ROOT / "packages" / "tangle-api" / "src" / "tangle_api" / "schema" / "openapi.json"
DEFAULT_OPENAPI_RESOURCE_PACKAGE = "tangle_api.schema"
DEFAULT_OPENAPI_RESOURCE_NAME = "openapi.json"
_FALLBACK_OPENAPI_RESOURCE_PACKAGES = (DEFAULT_OPENAPI_RESOURCE_PACKAGE, "tangle_cli.openapi")


def _load_json_file(schema_path: Path) -> dict[str, Any]:
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    if not isinstance(schema, dict) or "paths" not in schema:
        raise ValueError(f"{schema_path} does not look like an OpenAPI schema")
    return schema


def _load_default_openapi_schema() -> dict[str, Any]:
    if DEFAULT_OPENAPI_PATH.exists():
        return _load_json_file(DEFAULT_OPENAPI_PATH)

    schema_text = None
    schema_package = DEFAULT_OPENAPI_RESOURCE_PACKAGE
    last_error: Exception | None = None
    for package in _FALLBACK_OPENAPI_RESOURCE_PACKAGES:
        try:
            schema_text = (
                resources.files(package)
                .joinpath(DEFAULT_OPENAPI_RESOURCE_NAME)
                .read_text(encoding="utf-8")
            )
            schema_package = package
            break
        except (FileNotFoundError, ModuleNotFoundError) as exc:
            last_error = exc
    if schema_text is None:
        raise FileNotFoundError(
            "Default OpenAPI snapshot not found. Install the default or a compatible "
            "custom tangle-api package, run from a source checkout with "
            "packages/tangle-api/src/tangle_api/schema/openapi.json, or pass "
            "--openapi PATH explicitly."
        ) from last_error

    schema = json.loads(schema_text)
    if not isinstance(schema, dict) or "paths" not in schema:
        raise ValueError(
            f"{schema_package}/{DEFAULT_OPENAPI_RESOURCE_NAME} "
            "does not look like an OpenAPI schema"
        )
    return schema


def load_openapi_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Load a Tangle OpenAPI schema from disk."""

    if path is None:
        return _load_default_openapi_schema()
    return _load_json_file(Path(path))


def parsed_operations(schema: dict[str, Any] | None = None) -> list[OperationCommand]:
    """Return normalized operations using the same parser as the dynamic CLI."""

    return operation_commands(schema or load_openapi_schema())
