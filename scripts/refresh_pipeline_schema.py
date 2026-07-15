#!/usr/bin/env python3
"""Refresh the vendored Tangle pipeline JSON schema.

This maintenance helper intentionally does the pinned source fetch/generation at
refresh time, not at tangle-cli runtime. Review the generated schema diff before
committing changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import requests

TANGLE_STRUCTURES_COMMIT = "cf599dc45ec2cea2c0a8ae8a2d84af7985c5035c"
TANGLE_STRUCTURES_SHA256 = "86e3329fe27740093d8e7113fcf93ca2f8cad55c2db8a80938bdfefd56b813e0"
TANGLE_STRUCTURES_PATH = "cloud_pipelines_backend/component_structures.py"
TANGLE_STRUCTURES_URL = (
    f"https://raw.githubusercontent.com/TangleML/tangle/{TANGLE_STRUCTURES_COMMIT}/"
    f"{TANGLE_STRUCTURES_PATH}"
)
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "packages/tangle-cli/src/tangle_cli/schemas/pipeline_schema.json"


class TangleStructuresIntegrityError(RuntimeError):
    """Raised when fetched Tangle source does not match the pinned hash."""


def fetch_tangle_structures() -> str:
    response = requests.get(TANGLE_STRUCTURES_URL, timeout=10)
    response.raise_for_status()
    actual_sha256 = hashlib.sha256(response.content).hexdigest()
    if actual_sha256 != TANGLE_STRUCTURES_SHA256:
        raise TangleStructuresIntegrityError(
            f"SHA-256 mismatch for {TANGLE_STRUCTURES_URL}: expected "
            f"{TANGLE_STRUCTURES_SHA256}, got {actual_sha256}"
        )
    return response.text


def generate_schema(source_text: str) -> dict[str, Any]:
    import pydantic

    namespace: dict[str, Any] = {}
    exec(
        "import dataclasses\n"
        "from collections import OrderedDict\n"
        "from typing import Any, Dict, List, Mapping, Optional, Sequence, Union\n"
        "import pydantic\n"
        "import pydantic.alias_generators\n"
        "from pydantic.dataclasses import dataclass as pydantic_dataclasses\n",
        namespace,
    )
    exec(source_text, namespace)

    graph_spec = namespace.get("GraphSpec")
    if graph_spec is None:
        raise RuntimeError("GraphSpec was not found in component_structures.py")

    adapter = pydantic.TypeAdapter(graph_spec)
    adapter.rebuild(_types_namespace=namespace)
    graph_schema = adapter.json_schema()
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Tangle Pipeline Schema (generated from TangleML)",
        "type": "object",
        "required": ["name", "implementation"],
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "description": {"type": "string"},
            "metadata": {"type": "object"},
            "inputs": {"type": "array"},
            "outputs": {"type": "array"},
            "implementation": {
                "type": "object",
                "required": ["graph"],
                "properties": {"graph": graph_schema},
            },
        },
        "$defs": graph_schema.get("$defs", {}),
        "x-tangle-source": {
            "repository": "https://github.com/TangleML/tangle",
            "path": TANGLE_STRUCTURES_PATH,
            "commit": TANGLE_STRUCTURES_COMMIT,
            "sha256": TANGLE_STRUCTURES_SHA256,
            "generatedBy": "scripts/refresh_pipeline_schema.py",
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help=f"write {SCHEMA_PATH}")
    args = parser.parse_args()

    schema = generate_schema(fetch_tangle_structures())
    text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.write:
        SCHEMA_PATH.write_text(text, encoding="utf-8")
        print(f"wrote {SCHEMA_PATH}")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
