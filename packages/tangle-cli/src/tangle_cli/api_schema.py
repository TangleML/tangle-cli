"""OpenAPI schema cache and operation mapping utilities for Tangle APIs."""

from __future__ import annotations

import hashlib
import json
import keyword
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

import httpx
import platformdirs

from .api_transport import (
    DEFAULT_TIMEOUT_SECONDS,
    _normalize_base_url,
    _openapi_url,
    _request_headers,
    default_base_url,
)

SUPPORTED_METHODS = {"get", "post", "put", "patch", "delete"}
_HTTP_METHOD_NAMES = {
    "get": "get",
    "post": "create",
    "put": "update",
    "patch": "update",
    "delete": "delete",
}
_METHOD_PRIORITY = {
    "get": 0,
    "post": 1,
    "put": 2,
    "patch": 3,
    "delete": 4,
}


@dataclass(frozen=True)
class CliParameter:
    """Normalized OpenAPI parameter/body field for CLI and client dispatch."""

    original_name: str
    local_name: str
    location: Literal["path", "query", "body"]
    python_type: Any
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class OperationCommand:
    """Normalized OpenAPI operation ready for CLI or programmatic dispatch."""

    group_name: str
    command_name: str
    method: str
    path: str
    operation: dict[str, Any]
    parameters: tuple[CliParameter, ...]
    has_request_body: bool

    @property
    def operation_name(self) -> str:
        return f"{self.group_name}.{self.command_name}"


def default_cache_dir() -> Path:
    """Return the OpenAPI schema cache directory.

    ``TANGLE_CLI_CACHE_DIR`` is an explicit cache directory override for tests
    and automation. Otherwise platformdirs selects the OS-appropriate user
    cache directory and OpenAPI files live in an ``openapi`` subdirectory.
    """

    import os

    configured = os.environ.get("TANGLE_CLI_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(platformdirs.user_cache_dir("tangle-cli", "TangleML")) / "openapi"


def cache_path(base_url: str | None = None) -> Path:
    """Return the schema cache file for a base URL."""

    normalized = _normalize_base_url(base_url or default_base_url())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return default_cache_dir() / f"schema-{digest}.json"


def load_cached_schema(base_url: str | None = None) -> dict[str, Any] | None:
    """Load a previously fetched schema without touching the network."""

    path = cache_path(base_url)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_cached_schema(schema: dict[str, Any], base_url: str | None = None) -> Path:
    """Atomically write a schema cache file and return its path."""

    path = cache_path(base_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp_path.replace(path)
    return path


def fetch_schema(
    base_url: str | None = None,
    token: str | None = None,
    header: list[str] | str | None = None,
    auth_header: str | None = None,
    headers: dict[str, str] | None = None,
    include_env_credentials: bool = True,
) -> dict[str, Any]:
    """Fetch ``/openapi.json``, applying bearer and custom auth headers."""

    base_url = _normalize_base_url(base_url or default_base_url())
    response = httpx.get(
        _openapi_url(base_url),
        headers=_request_headers(
            token,
            header,
            auth_header,
            headers,
            include_env_credentials=include_env_credentials,
        ),
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.text
    schema = json.loads(payload)
    if not isinstance(schema, dict) or "paths" not in schema:
        raise RuntimeError("OpenAPI response did not contain a paths object")
    return schema


def refresh_schema(
    base_url: str | None = None,
    token: str | None = None,
    header: list[str] | str | None = None,
    auth_header: str | None = None,
    headers: dict[str, str] | None = None,
    include_env_credentials: bool = True,
) -> tuple[dict[str, Any], Path]:
    """Fetch and cache the latest schema for a backend."""

    base_url = _normalize_base_url(base_url or default_base_url())
    schema = fetch_schema(
        base_url,
        token,
        header,
        auth_header,
        headers,
        include_env_credentials=include_env_credentials,
    )
    path = write_cached_schema(schema, base_url)
    return schema, path


def load_or_fetch_schema(
    base_url: str | None = None,
    token: str | None = None,
    header: list[str] | str | None = None,
    auth_header: str | None = None,
    headers: dict[str, str] | None = None,
    include_env_credentials: bool = True,
) -> dict[str, Any]:
    """Use a cached schema when available, otherwise fetch once and cache it."""

    cached = load_cached_schema(base_url)
    if cached is not None:
        return cached
    schema, _ = refresh_schema(
        base_url,
        token,
        header,
        auth_header,
        headers,
        include_env_credentials=include_env_credentials,
    )
    return schema


def operation_commands(schema: dict[str, Any]) -> list[OperationCommand]:
    """Return normalized operations with deterministic collision handling applied."""

    operations: list[OperationCommand] = []
    used_names: dict[str, dict[str, OperationCommand]] = {}
    for operation in _iter_operation_commands(schema):
        group_names = used_names.setdefault(operation.group_name, {})
        command_name = _dedupe_command_name(operation.command_name, group_names, operation)
        if command_name != operation.command_name:
            operation = replace(operation, command_name=command_name)
        operations.append(operation)
    return operations


def operation_map(schema: dict[str, Any]) -> dict[str, OperationCommand]:
    """Return operations keyed by canonical ``group.command`` name."""

    return {operation.operation_name: operation for operation in operation_commands(schema)}


def operation_aliases(operation_name: str) -> set[str]:
    """Return Python-friendly aliases for a canonical operation name."""

    aliases = {operation_name}
    aliases.add(operation_name.replace("-", "_"))
    aliases.add(operation_name.replace("_", "-"))
    if "." in operation_name:
        group, command = operation_name.split(".", 1)
        aliases.add(f"{group.replace('-', '_')}.{command.replace('-', '_')}")
        aliases.add(f"{group.replace('_', '-')}.{command.replace('_', '-')}")
    return aliases


def resolve_operation(
    operations: dict[str, OperationCommand], operation_name: str
) -> OperationCommand:
    """Resolve canonical or Python-friendly operation names."""

    candidates = [operation_name, operation_name.replace("_", "-"), operation_name.replace("-", "_")]
    if "." in operation_name:
        group, command = operation_name.split(".", 1)
        candidates.extend(
            [
                f"{group.replace('_', '-')}.{command.replace('_', '-')}",
                f"{group.replace('-', '_')}.{command.replace('-', '_')}",
            ]
        )
    for candidate in candidates:
        if candidate in operations:
            return operations[candidate]
    aliases: dict[str, OperationCommand] = {}
    for name, operation in operations.items():
        for alias in operation_aliases(name):
            aliases.setdefault(alias, operation)
    if operation_name in aliases:
        return aliases[operation_name]
    raise KeyError(f"Unknown Tangle API operation: {operation_name}")


def _iter_operation_commands(schema: dict[str, Any]) -> list[OperationCommand]:
    """Convert OpenAPI path/method entries into normalized operation specs."""

    operations: list[OperationCommand] = []
    paths = schema.get("paths", {})
    if not isinstance(paths, dict):
        return operations

    for path, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        path_level_parameters = path_item.get("parameters") or []
        for method, operation in sorted(path_item.items(), key=_method_sort_key):
            method_lower = method.lower()
            if method_lower not in SUPPORTED_METHODS or not isinstance(operation, dict):
                continue

            group_name = _operation_group_name(operation, path)
            command_name = _operation_command_name(method_lower, path, group_name)
            parameters = _operation_parameters(
                schema, path_level_parameters, operation, path
            )
            has_request_body, body_parameters = _request_body_parameters(
                schema, operation, {p.local_name for p in parameters}
            )
            operations.append(
                OperationCommand(
                    group_name=group_name,
                    command_name=command_name,
                    method=method_lower.upper(),
                    path=path,
                    operation=operation,
                    parameters=tuple(parameters + body_parameters),
                    has_request_body=has_request_body,
                )
            )

    return operations


def _operation_group_name(operation: dict[str, Any], path: str) -> str:
    """Choose the CLI/client group from the resource path, falling back to tags."""

    for part in _path_parts(path):
        if part != "api" and not _is_path_param(part):
            return _normalize_name(part)

    tags = operation.get("tags")
    if isinstance(tags, list) and tags:
        return _normalize_name(str(tags[0]))
    return "api"


def _operation_command_name(method: str, path: str, group_name: str) -> str:
    """Derive a readable command name from HTTP method and path shape."""

    parts = _path_parts(path)
    parts_without_api = parts[1:] if parts and parts[0] == "api" else parts

    resource_index = None
    for index, part in enumerate(parts_without_api):
        if _normalize_name(part) == group_name:
            resource_index = index
            break

    if resource_index is None:
        for index, part in enumerate(parts_without_api):
            if not _is_path_param(part):
                resource_index = index
                break

    remainder = (
        parts_without_api[resource_index + 1 :]
        if resource_index is not None
        else parts_without_api
    )
    path_param_count = sum(1 for part in remainder if _is_path_param(part))
    static_segments = [_normalize_name(part) for part in remainder if not _is_path_param(part)]

    if static_segments:
        return "-".join(static_segments)

    if path_param_count == 0:
        return "list" if method == "get" else _HTTP_METHOD_NAMES.get(method, method)

    return _HTTP_METHOD_NAMES.get(method, method)


def _operation_parameters(
    schema: dict[str, Any],
    path_level_parameters: list[Any],
    operation: dict[str, Any],
    path: str,
) -> list[CliParameter]:
    """Collect OpenAPI path/query params for CLI positionals and client kwargs."""

    parameters: list[CliParameter] = []
    used_names: set[str] = {"base_url", "token", "auth_header", "header", "headers", "body"}
    operation_parameters = list(path_level_parameters) + list(operation.get("parameters") or [])

    for parameter in operation_parameters:
        parameter = _resolve_ref(schema, parameter)
        if not isinstance(parameter, dict):
            continue
        location = parameter.get("in")
        if location not in {"path", "query"}:
            continue
        original_name = str(parameter.get("name") or "value")
        required = bool(parameter.get("required") or location == "path")
        parameter_schema = _unwrap_nullable_schema(
            schema, parameter.get("schema") or {}
        )
        default = parameter_schema.get("default") if isinstance(parameter_schema, dict) else None
        description = str(parameter.get("description") or "")
        local_name = _safe_identifier(original_name, used_names, location)
        parameters.append(
            CliParameter(
                original_name=original_name,
                local_name=local_name,
                location=location,  # type: ignore[arg-type]
                python_type=_schema_to_python_type(schema, parameter_schema),
                required=required,
                default=default,
                description=description,
            )
        )

    for original_name in re.findall(r"{([^}]+)}", path):
        if any(p.location == "path" and p.original_name == original_name for p in parameters):
            continue
        local_name = _safe_identifier(original_name, used_names, "path")
        parameters.append(
            CliParameter(
                original_name=original_name,
                local_name=local_name,
                location="path",
                python_type=str,
                required=True,
            )
        )

    return parameters


def _request_body_parameters(
    schema: dict[str, Any], operation: dict[str, Any], used_names: set[str] | None = None
) -> tuple[bool, list[CliParameter]]:
    """Expose simple JSON object body fields as CLI options/client kwargs."""

    request_body = _resolve_ref(schema, operation.get("requestBody") or {})
    if not isinstance(request_body, dict) or not request_body:
        return False, []

    body_schema = _json_request_body_schema(schema, request_body)
    if not body_schema:
        return True, []

    body_schema = _flatten_schema(schema, body_schema)
    properties = body_schema.get("properties") or {}
    if not isinstance(properties, dict):
        return True, []

    required_fields = set(body_schema.get("required") or [])
    used_names = set(used_names or set()) | {"base_url", "token", "auth_header", "header", "headers", "body"}
    parameters: list[CliParameter] = []
    for original_name, property_schema in sorted(properties.items()):
        property_schema = _flatten_schema(schema, property_schema)
        if not _is_simple_schema(schema, property_schema):
            continue
        local_name = _safe_identifier(str(original_name), used_names, "body")
        default = property_schema.get("default") if isinstance(property_schema, dict) else None
        parameters.append(
            CliParameter(
                original_name=str(original_name),
                local_name=local_name,
                location="body",
                python_type=_schema_to_python_type(schema, property_schema),
                required=str(original_name) in required_fields,
                default=default,
                description=str(property_schema.get("description") or ""),
            )
        )
    return True, parameters


def _json_request_body_schema(
    schema: dict[str, Any], request_body: dict[str, Any]
) -> dict[str, Any] | None:
    """Return the JSON media-type schema for a request body, if any."""

    content = request_body.get("content") or {}
    if not isinstance(content, dict):
        return None
    media = content.get("application/json")
    if media is None:
        media = next(
            (
                value
                for key, value in content.items()
                if key == "application/*+json" or key.endswith("+json")
            ),
            None,
        )
    if not isinstance(media, dict):
        return None
    media_schema = media.get("schema")
    if not isinstance(media_schema, dict):
        return None
    return _resolve_ref(schema, media_schema)


def _flatten_schema(schema: dict[str, Any], value: Any) -> dict[str, Any]:
    """Merge simple ``allOf`` object schemas so body fields can become options."""

    value = _unwrap_nullable_schema(schema, value)
    if not isinstance(value, dict):
        return {}
    if "allOf" not in value:
        return value

    flattened: dict[str, Any] = {k: v for k, v in value.items() if k != "allOf"}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in value.get("allOf") or []:
        item = _flatten_schema(schema, item)
        properties.update(item.get("properties") or {})
        required.extend(item.get("required") or [])
    properties.update(flattened.get("properties") or {})
    required.extend(flattened.get("required") or [])
    if properties:
        flattened["properties"] = properties
    if required:
        flattened["required"] = sorted(set(required))
    return flattened


def _is_simple_schema(schema_doc: dict[str, Any], schema: Any) -> bool:
    """Return true for scalar/list types safe to expose as CLI options."""

    schema = _unwrap_nullable_schema(schema_doc, schema)
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type in {"string", "integer", "number", "boolean"}:
        return True
    if schema_type == "array":
        return _is_simple_schema(schema_doc, schema.get("items") or {})
    return False


def _schema_to_python_type(schema_doc: dict[str, Any], schema: Any) -> Any:
    """Map a small OpenAPI schema subset to Python annotations for Cyclopts."""

    schema = _unwrap_nullable_schema(schema_doc, schema)
    if not isinstance(schema, dict):
        return str
    schema_type = schema.get("type")
    if schema_type == "integer":
        return int
    if schema_type == "number":
        return float
    if schema_type == "boolean":
        return bool
    if schema_type == "array":
        return list[_schema_to_python_type(schema_doc, schema.get("items") or {})]
    return str


def _method_sort_key(item: tuple[str, Any]) -> tuple[int, str]:
    method = item[0].lower()
    return (_METHOD_PRIORITY.get(method, 100), method)


def _path_parts(path: str) -> list[str]:
    return [part for part in path.strip("/").split("/") if part]


def _is_path_param(part: str) -> bool:
    return part.startswith("{") and part.endswith("}")


def _normalize_name(value: str) -> str:
    """Normalize OpenAPI tag/path text to kebab-case CLI names."""

    value = value.strip().replace("_", "-").replace(" ", "-")
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", value)
    value = re.sub(r"[^A-Za-z0-9-]+", "-", value)
    value = re.sub(r"-+", "-", value).strip("-").lower()
    return value or "api"


def _safe_identifier(original: str, used_names: set[str], prefix: str) -> str:
    """Convert OpenAPI parameter names into unique Python identifiers."""

    name = _normalize_name(original).replace("-", "_")
    if not name or name[0].isdigit() or keyword.iskeyword(name):
        name = f"{prefix}_{name or 'value'}"
    candidate = name
    suffix = 2
    while candidate in used_names:
        candidate = f"{name}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _dedupe_command_name(
    command_name: str,
    used_names: dict[str, OperationCommand],
    operation: OperationCommand,
) -> str:
    """Avoid command collisions within a resource group."""

    existing = used_names.get(command_name)
    if existing is None or _same_operation(existing, operation):
        used_names[command_name] = operation
        return command_name

    method_prefix = operation.method.lower()
    candidate = f"{method_prefix}-{command_name}"
    existing = used_names.get(candidate)
    if existing is None or _same_operation(existing, operation):
        used_names[candidate] = operation
        return candidate

    path_suffix = "-".join(_normalize_name(part) for part in _path_parts(operation.path))
    candidate = f"{method_prefix}-{path_suffix}"
    suffix = 2
    while candidate in used_names and not _same_operation(used_names[candidate], operation):
        candidate = f"{method_prefix}-{path_suffix}-{suffix}"
        suffix += 1
    used_names[candidate] = operation
    return candidate


def _same_operation(left: OperationCommand, right: OperationCommand) -> bool:
    return left.method == right.method and left.path == right.path


def _unwrap_nullable_schema(schema: dict[str, Any], value: Any) -> Any:
    """Resolve refs and reduce nullable unions to their non-null schema."""

    value = _resolve_ref(schema, value)
    if not isinstance(value, dict):
        return value

    schema_type = value.get("type")
    if isinstance(schema_type, list):
        non_null_types = [item for item in schema_type if item != "null"]
        if len(non_null_types) == 1:
            value = {**value, "type": non_null_types[0]}

    for union_key in ("anyOf", "oneOf"):
        variants = value.get(union_key)
        if not isinstance(variants, list):
            continue
        for variant in variants:
            variant = _resolve_ref(schema, variant)
            if not isinstance(variant, dict) or variant.get("type") == "null":
                continue
            metadata = {k: v for k, v in value.items() if k not in {union_key, "type"}}
            return {**variant, **metadata}
    return value


def _resolve_ref(schema: dict[str, Any], value: Any) -> Any:
    """Resolve local OpenAPI ``$ref`` pointers; leave unsupported refs untouched."""

    if not isinstance(value, dict) or "$ref" not in value:
        return value
    ref = value["$ref"]
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return value
    current: Any = schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict):
            return value
        current = current.get(part)
    return current if current is not None else value
