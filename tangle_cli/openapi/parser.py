"""Offline OpenAPI loading helpers used by generated-client codegen.

The runtime client does not import this module. It exists so expanding the
checked-in generated client is a deterministic local operation over the
checked-in ``openapi.json`` snapshot.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tangle_cli.api_schema import OperationCommand, operation_commands

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_OPENAPI_PATH = PACKAGE_DIR / "openapi.json"


def load_openapi_schema(path: str | Path | None = None) -> dict[str, Any]:
    """Load a Tangle OpenAPI schema from disk."""

    schema_path = Path(path) if path is not None else DEFAULT_OPENAPI_PATH
    with schema_path.open("r", encoding="utf-8") as f:
        schema = json.load(f)
    if not isinstance(schema, dict) or "paths" not in schema:
        raise ValueError(f"{schema_path} does not look like an OpenAPI schema")
    return schema


def parsed_operations(schema: dict[str, Any] | None = None) -> list[OperationCommand]:
    """Return normalized operations using the same parser as the dynamic CLI."""

    return operation_commands(schema or load_openapi_schema())
