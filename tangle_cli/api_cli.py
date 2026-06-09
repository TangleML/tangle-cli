"""OpenAPI-backed `tangle api` command implementation.

The backend exposes a FastAPI OpenAPI schema. This module caches that schema
locally, maps tags/paths/parameters into a Cyclopts command tree, and dispatches
invocations as HTTP requests. Commands are generated at import time only from a
cached schema, or after a one-time fetch when the user explicitly asks for API
help/commands, so normal CLI startup does not require a running backend.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import keyword
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import platformdirs
from cyclopts import App, Parameter

DEFAULT_API_URL = "http://localhost:8000"
DEFAULT_TIMEOUT_SECONDS = 30.0
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

BaseUrlOption = Annotated[
    str | None,
    Parameter(
        help=(
            "Tangle API base URL. Defaults to TANGLE_API_URL, then "
            f"{DEFAULT_API_URL}."
        )
    ),
]
TokenOption = Annotated[
    str | None,
    Parameter(help="Bearer token. Defaults to TANGLE_API_TOKEN."),
]
AuthHeaderOption = Annotated[
    str | None,
    Parameter(
        help=(
            "Authorization header value, e.g. 'Bearer TOKEN' or 'Basic BASE64'. "
            "Defaults to TANGLE_API_AUTH_HEADER or TANGLE_AUTH_HEADER."
        )
    ),
]
HeaderOption = Annotated[
    list[str] | None,
    Parameter(
        alias="-H",
        help=(
            "Custom request header as 'Name: value'. Repeat for multiple. "
            "Applied after TANGLE_API_HEADERS."
        ),
        negative_iterable=(),
    ),
]
BodyOption = Annotated[
    str | None,
    Parameter(help="JSON request body, or @path/to/file.json."),
]


@dataclass(frozen=True)
class CliParameter:
    """Normalized OpenAPI parameter/body field ready for Cyclopts."""
    original_name: str
    local_name: str
    location: Literal["path", "query", "body"]
    python_type: Any
    required: bool = False
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class OperationCommand:
    """Normalized OpenAPI operation ready to become one CLI command."""
    group_name: str
    command_name: str
    method: str
    path: str
    operation: dict[str, Any]
    parameters: tuple[CliParameter, ...]
    has_request_body: bool


def default_base_url() -> str:
    return _normalize_base_url(os.environ.get("TANGLE_API_URL") or DEFAULT_API_URL)


def default_token() -> str | None:
    return os.environ.get("TANGLE_API_TOKEN") or None


def default_auth_header() -> str | None:
    return os.environ.get("TANGLE_API_AUTH_HEADER") or os.environ.get("TANGLE_AUTH_HEADER") or None


_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


def default_cache_dir() -> Path:
    """Return the schema cache directory.

    `TANGLE_CLI_CACHE_DIR` is an explicit escape hatch for tests/automation.
    Otherwise platformdirs selects the OS-appropriate user cache directory and
    we keep OpenAPI files in an `openapi` subdirectory.
    """

    configured = os.environ.get("TANGLE_CLI_CACHE_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path(platformdirs.user_cache_dir("tangle-cli", "TangleML")) / "openapi"


def cache_path(base_url: str | None = None) -> Path:
    """Return the cache file for a base URL.

    The base URL hash keeps schemas for multiple Tangle backends separate while
    avoiding URL characters in filenames.
    """

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
    header: list[str] | None = None,
    auth_header: str | None = None,
) -> dict[str, Any]:
    """Fetch `/openapi.json`, applying bearer and custom auth headers."""

    base_url = _normalize_base_url(base_url or default_base_url())
    token = token or default_token()
    response = httpx.get(
        _openapi_url(base_url),
        headers=_request_headers(token, header, auth_header),
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
    header: list[str] | None = None,
    auth_header: str | None = None,
) -> tuple[dict[str, Any], Path]:
    """Fetch and cache the latest schema for a backend."""

    base_url = _normalize_base_url(base_url or default_base_url())
    schema = fetch_schema(base_url, token, header, auth_header)
    path = write_cached_schema(schema, base_url)
    return schema, path


def load_or_fetch_schema(
    base_url: str | None = None,
    token: str | None = None,
    header: list[str] | None = None,
    auth_header: str | None = None,
) -> dict[str, Any]:
    """Use a cached schema when available, otherwise fetch once and cache it."""

    cached = load_cached_schema(base_url)
    if cached is not None:
        return cached
    schema, _ = refresh_schema(base_url, token, header, auth_header)
    return schema


def build_app(schema: dict[str, Any] | None = None) -> App:
    """Build the `tangle api` Cyclopts app.

    When *schema* is supplied, dynamic commands are generated from it. The module-level
    app intentionally only auto-fetches a schema for invocations that target `tangle api`,
    so unrelated commands such as `tangle --help` do not hit the backend.
    """

    api_app = App(
        name="api",
        help="Call Tangle backend API endpoints from the cached OpenAPI schema.",
    )
    _register_refresh_command(api_app)

    schema = schema if schema is not None else _schema_for_current_invocation()
    if schema is not None:
        register_dynamic_commands(api_app, schema)

    return api_app


def register_dynamic_commands(api_app: App, schema: dict[str, Any]) -> None:
    """Attach generated resource groups and endpoint commands to `api_app`."""

    groups: dict[str, App] = {}
    used_names: dict[str, dict[str, OperationCommand]] = {}

    for operation in _iter_operation_commands(schema):
        group = groups.get(operation.group_name)
        if group is None:
            group = App(
                name=operation.group_name,
                help=f"Call {operation.group_name} API endpoints.",
            )
            groups[operation.group_name] = group
            used_names[operation.group_name] = {}
            api_app.command(group)

        command_name = _dedupe_command_name(
            operation.command_name, used_names[operation.group_name], operation
        )
        command = _make_operation_callable(operation)
        group.command(command, name=command_name)


def _register_refresh_command(api_app: App) -> None:
    @api_app.command(name="refresh")
    def refresh(
        *,
        base_url: BaseUrlOption = None,
        token: TokenOption = None,
        auth_header: AuthHeaderOption = None,
        header: HeaderOption = None,
    ) -> None:
        """Fetch /openapi.json and update the local schema cache."""

        normalized_base_url = _normalize_base_url(base_url or default_base_url())
        try:
            schema, path = refresh_schema(normalized_base_url, token, header, auth_header)
        except httpx.HTTPStatusError as exc:
            message = exc.response.text or exc.response.reason_phrase
            raise SystemExit(
                f"Failed to fetch {_openapi_url(normalized_base_url)}: {message}"
            ) from exc
        except httpx.RequestError as exc:
            raise SystemExit(
                f"Failed to fetch {_openapi_url(normalized_base_url)}: {exc}"
            ) from exc
        path_count = len(schema.get("paths", {}))
        print(f"Cached OpenAPI schema for {normalized_base_url}")
        print(f"Path: {path}")
        print(f"OpenAPI paths: {path_count}")


def _iter_operation_commands(schema: dict[str, Any]) -> list[OperationCommand]:
    """Convert OpenAPI path/method entries into normalized command specs."""

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
    """Choose the CLI group from the resource path, falling back to tags.

    Tangle reuses broad tags such as `components` for several resources
    (`components`, `published_components`, `component_libraries`, ...). The
    first meaningful `/api/...` path segment is the stable resource family and
    prevents unrelated endpoints from colliding under one tag-based group.
    """

    for part in _path_parts(path):
        if part != "api" and not _is_path_param(part):
            return _normalize_name(part)

    tags = operation.get("tags")
    if isinstance(tags, list) and tags:
        return _normalize_name(str(tags[0]))
    return "api"


def _operation_command_name(method: str, path: str, group_name: str) -> str:
    """Derive a readable command name from HTTP method and path shape.

    Collection endpoints become `list`/`create`, item endpoints become
    `get`/`update`/`delete`, and action suffixes such as `/cancel` or `/details`
    become the command name.
    """

    parts = _path_parts(path)
    parts_without_api = parts[1:] if parts and parts[0] == "api" else parts

    resource_index = None
    for index, part in enumerate(parts_without_api):
        if _normalize_name(part) == group_name:
            resource_index = index
            break

    if resource_index is None:
        # Fall back to the first non-parameter segment when the tag does not match the path.
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
    """Collect OpenAPI path/query params for CLI positionals and options."""

    parameters: list[CliParameter] = []
    used_names: set[str] = {"base_url", "token", "auth_header", "header", "body"}
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
                python_type=_schema_to_python_type(parameter_schema),
                required=required,
                default=default,
                description=description,
            )
        )

    # Some FastAPI path params can be omitted from the OpenAPI parameter list in tests
    # or hand-authored schemas. Ensure placeholders are still surfaced as positionals.
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
    """Expose simple JSON object body fields as options when practical.

    Complex bodies still get the generic `--body` escape hatch, so unsupported
    schema shapes remain callable.
    """

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
    used_names = set(used_names or set()) | {"base_url", "token", "auth_header", "header", "body"}
    parameters: list[CliParameter] = []
    for original_name, property_schema in sorted(properties.items()):
        property_schema = _flatten_schema(schema, property_schema)
        if not _is_simple_schema(property_schema):
            continue
        local_name = _safe_identifier(str(original_name), used_names, "body")
        default = property_schema.get("default") if isinstance(property_schema, dict) else None
        parameters.append(
            CliParameter(
                original_name=str(original_name),
                local_name=local_name,
                location="body",
                python_type=_schema_to_python_type(property_schema),
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
    """Merge simple `allOf` object schemas so body fields can become options."""

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


def _is_simple_schema(schema: Any) -> bool:
    """Return true for scalar/list types safe to expose as CLI options."""

    schema = _unwrap_nullable_schema({}, schema)
    if not isinstance(schema, dict):
        return False
    schema_type = schema.get("type")
    if schema_type in {"string", "integer", "number", "boolean"}:
        return True
    if schema_type == "array":
        items = schema.get("items") or {}
        return isinstance(items, dict) and items.get("type") in {
            "string",
            "integer",
            "number",
            "boolean",
        }
    return False


def _schema_to_python_type(schema: Any) -> Any:
    """Map a small OpenAPI schema subset to Python annotations for Cyclopts."""

    schema = _unwrap_nullable_schema({}, schema)
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
        return list[_schema_to_python_type(schema.get("items") or {})]
    return str


def _make_operation_callable(operation: OperationCommand):
    """Create the Python callable Cyclopts registers for one endpoint.

    Cyclopts introspects function metadata, so we attach a generated signature
    and docstring below. The real function accepts flexible args/kwargs and
    forwards normalized values to the HTTP dispatcher.
    """

    positional_names = [
        parameter.local_name
        for parameter in operation.parameters
        if parameter.location == "path"
    ]

    def command(*args: Any, **values: Any) -> None:
        for name, value in zip(positional_names, args):
            values[name] = value
        _invoke_operation(operation, values)

    command.__name__ = _safe_function_name(f"{operation.group_name}_{operation.command_name}")
    command.__doc__ = _operation_help(operation)
    command.__signature__ = _operation_signature(operation)  # type: ignore[attr-defined]
    return command


def _operation_signature(operation: OperationCommand) -> inspect.Signature:
    """Build the signature Cyclopts uses for parsing and help output.

    Path parameters are positional. Query parameters and simple body fields are
    keyword-only options. `--body`, `--header`, `--auth-header`, `--base-url`,
    and `--token` are appended as common generated-command options.
    """

    parameters: list[inspect.Parameter] = []

    for parameter in operation.parameters:
        if parameter.location != "path":
            continue
        annotation = _annotated_type(parameter.python_type, parameter.description)
        parameters.append(
            inspect.Parameter(
                parameter.local_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=annotation,
            )
        )

    for parameter in operation.parameters:
        if parameter.location not in {"query", "body"}:
            continue
        body_field_with_escape_hatch = parameter.location == "body" and operation.has_request_body
        annotation = _annotated_type(
            _optional_type(parameter.python_type)
            if not parameter.required or body_field_with_escape_hatch
            else parameter.python_type,
            parameter.description,
        )
        default = (
            inspect.Parameter.empty
            if parameter.required and not body_field_with_escape_hatch
            else parameter.default
        )
        parameters.append(
            inspect.Parameter(
                parameter.local_name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )

    if operation.has_request_body:
        parameters.append(
            inspect.Parameter(
                "body",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=BodyOption,
            )
        )

    parameters.append(
        inspect.Parameter(
            "auth_header",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=AuthHeaderOption,
        )
    )
    parameters.append(
        inspect.Parameter(
            "header",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=HeaderOption,
        )
    )

    parameters.extend(
        [
            inspect.Parameter(
                "base_url",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=BaseUrlOption,
            ),
            inspect.Parameter(
                "token",
                inspect.Parameter.KEYWORD_ONLY,
                default=None,
                annotation=TokenOption,
            ),
        ]
    )
    return inspect.Signature(parameters=parameters)


def _optional_type(python_type: Any) -> Any:
    return python_type | None


def _annotated_type(python_type: Any, description: str) -> Any:
    if description:
        return Annotated[python_type, Parameter(help=description)]
    return python_type


def _operation_help(operation: OperationCommand) -> str:
    summary = operation.operation.get("summary") or operation.operation.get("description")
    if summary:
        return str(summary).strip()
    return f"{operation.method} {operation.path}"


def _request_headers(
    token: str | None,
    cli_header_entries: list[str] | None,
    cli_auth_header: str | None,
) -> dict[str, str]:
    """Build request headers without printing or otherwise exposing secrets.

    `TANGLE_API_HEADERS` supports arbitrary auth schemes from wrappers and
    hosted environments (for example `Cloud-Auth`). `--token` remains the
    convenient bearer-token path. `--auth-header` follows the reference CLI and
    sets the Authorization header directly, and repeated `--header` flags can
    override any earlier source for one command.
    """

    headers = {"Accept": "application/json"}
    headers.update(_headers_from_env())
    env_auth_header = default_auth_header()
    if env_auth_header:
        headers["Authorization"] = _normalize_auth_header(
            env_auth_header, "TANGLE_API_AUTH_HEADER"
        )
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cli_auth_header:
        headers["Authorization"] = _normalize_auth_header(cli_auth_header, "--auth-header")
    headers.update(_parse_header_entries(cli_header_entries or [], "--header"))
    return headers


def _normalize_auth_header(raw: str, source: str) -> str:
    """Accept either an Authorization value or `Authorization: value`."""

    value = raw.strip()
    if value.lower().startswith("authorization:"):
        value = value.split(":", 1)[1].strip()
    if not value or "\n" in value or "\r" in value:
        raise SystemExit(f"Invalid {source}; expected an authorization header value")
    return value


def _headers_from_env() -> dict[str, str]:
    raw = os.environ.get("TANGLE_API_HEADERS")
    if not raw or not raw.strip():
        return {}
    return _parse_header_entries(_env_header_entries(raw), "TANGLE_API_HEADERS")


def _env_header_entries(raw: str) -> list[str]:
    """Parse env headers as JSON object/list or newline-separated entries."""

    raw = raw.strip()
    if raw[0] in "[{":
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SystemExit("Invalid TANGLE_API_HEADERS JSON") from exc
        if isinstance(parsed, dict):
            return [f"{name}: {value}" for name, value in parsed.items()]
        if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
            return parsed
        raise SystemExit("TANGLE_API_HEADERS must be a JSON object or string list")
    return [line.strip() for line in raw.splitlines() if line.strip()]


def _parse_header_entries(entries: list[str], source: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for entry in entries:
        if ":" in entry:
            name, value = entry.split(":", 1)
        elif "=" in entry:
            name, value = entry.split("=", 1)
        else:
            raise SystemExit(f"Invalid {source} entry; expected 'Name: value'")
        name = name.strip()
        value = value.strip()
        if not name or not _HEADER_NAME_RE.fullmatch(name) or "\n" in value or "\r" in value:
            raise SystemExit(f"Invalid {source} header name or value")
        headers[name] = value
    return headers


def _invoke_operation(operation: OperationCommand, values: dict[str, Any]) -> None:
    """Turn parsed CLI values into an HTTP request and print the response.

    Path params are URL-escaped into the path template, query params are encoded
    onto the URL, body-field options are merged with `--body`, and JSON responses
    are pretty-printed.
    """

    base_url = _normalize_base_url(values.pop("base_url", None) or default_base_url())
    token = values.pop("token", None) or default_token()
    auth_header = values.pop("auth_header", None)
    header_entries = values.pop("header", None)
    body_arg = values.pop("body", None) if operation.has_request_body else None

    path = operation.path
    query: dict[str, Any] = {}
    body_fields: dict[str, Any] = {}
    for parameter in operation.parameters:
        value = values.pop(parameter.local_name, None)
        if value is None:
            continue
        if parameter.location == "path":
            path = path.replace(
                "{" + parameter.original_name + "}",
                urllib.parse.quote(str(value), safe=""),
            )
        elif parameter.location == "query":
            query[parameter.original_name] = value
        elif parameter.location == "body":
            body_fields[parameter.original_name] = value

    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    if query:
        url = f"{url}?{_urlencode_query(query)}"

    request_body = _load_body_argument(body_arg) if body_arg else None
    if body_fields:
        if request_body is None:
            request_body = {}
        if not isinstance(request_body, dict):
            raise SystemExit("--body must be a JSON object when body field options are used")
        request_body.update(body_fields)

    request_data = None
    headers = _request_headers(token, header_entries, auth_header)
    if request_body is not None:
        request_data = json.dumps(request_body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    try:
        response = httpx.request(
            operation.method,
            url,
            content=request_data,
            headers=headers,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        message = exc.response.text or exc.response.reason_phrase
        print(message, file=sys.stderr)
        raise SystemExit(exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise SystemExit(f"Failed to call {url}: {exc}") from exc

    if not response.content:
        return
    text = response.text
    if "json" in response.headers.get("Content-Type", "").lower():
        try:
            print(json.dumps(json.loads(text), indent=2, sort_keys=True))
            return
        except json.JSONDecodeError:
            pass
    print(text)


def _urlencode_query(query: dict[str, Any]) -> str:
    """Encode query params, preserving repeated values for list options."""

    items: list[tuple[str, Any]] = []
    for key, value in query.items():
        if isinstance(value, (list, tuple)):
            items.extend((key, item) for item in value)
        else:
            items.append((key, value))
    return urllib.parse.urlencode(items, doseq=True)


def _load_body_argument(body: str) -> Any:
    """Parse `--body`; a leading `@` reads JSON from a file path."""

    if body.startswith("@"):
        body = Path(body[1:]).expanduser().read_text(encoding="utf-8")
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON body: {exc}") from exc


def _schema_for_current_invocation() -> dict[str, Any] | None:
    """Return schema needed to build dynamic commands for this process.

    We prefer the cache and only fetch on API-focused invocations. If the user
    is dispatching a dynamic command, fetch failures are made actionable instead
    of letting Cyclopts report that only the static `refresh` command exists.
    """

    base_url = _base_url_from_argv(sys.argv) or default_base_url()
    cached = load_cached_schema(base_url)
    if cached is not None:
        return cached
    if not _argv_requests_api_schema(sys.argv):
        return None
    try:
        return load_or_fetch_schema(
            base_url,
            _token_from_argv(sys.argv),
            _headers_from_argv(sys.argv),
            _auth_header_from_argv(sys.argv),
        )
    except Exception as exc:
        if _argv_dispatches_dynamic_command(sys.argv):
            raise SystemExit(_schema_fetch_failure_message(base_url, exc)) from exc
        # Keep `tangle api --help` usable if the backend is unavailable; the
        # explicit `refresh` command or an attempted dynamic command reports the
        # concrete fetch failure and next step.
        return None


def _argv_requests_api_schema(argv: list[str]) -> bool:
    args = list(argv[1:])
    if "api" not in args:
        return False
    first_command = _api_first_command(args[args.index("api") + 1 :])
    return first_command != "refresh"


def _argv_dispatches_dynamic_command(argv: list[str]) -> bool:
    args = list(argv[1:])
    if "api" not in args:
        return False
    first_command = _api_first_command(args[args.index("api") + 1 :])
    return first_command not in {None, "refresh"}


def _api_first_command(api_tail: list[str]) -> str | None:
    skip_next = False
    options_with_values = {"--base-url", "--api-url", "--token", "--auth-header", "--header", "-H"}
    for arg in api_tail:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg in {"--help", "-h"}:
            return None
        if arg.startswith("--"):
            continue
        return arg
    return None


def _schema_fetch_failure_message(base_url: str, exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        reason = f"HTTP {exc.response.status_code} {exc.response.reason_phrase}"
    elif isinstance(exc, httpx.RequestError):
        reason = str(exc)
    else:
        reason = exc.__class__.__name__
    return (
        f"No cached OpenAPI schema for {_normalize_base_url(base_url)}, and fetching "
        f"{_openapi_url(base_url)} failed: {reason}. Run `tangle api refresh` "
        "with the same --base-url/--auth-header/--header options, or set "
        "TANGLE_API_URL/TANGLE_API_AUTH_HEADER/TANGLE_API_HEADERS."
    )


def _base_url_from_argv(argv: list[str]) -> str | None:
    return _option_from_argv(argv, "--base-url") or _option_from_argv(argv, "--api-url")


def _token_from_argv(argv: list[str]) -> str | None:
    return _option_from_argv(argv, "--token") or default_token()


def _auth_header_from_argv(argv: list[str]) -> str | None:
    return _option_from_argv(argv, "--auth-header") or default_auth_header()


def _headers_from_argv(argv: list[str]) -> list[str]:
    entries: list[str] = []
    for index, arg in enumerate(argv):
        if arg in {"--header", "-H"} and index + 1 < len(argv):
            entries.append(argv[index + 1])
        elif arg.startswith("--header="):
            entries.append(arg.split("=", 1)[1])
    return entries


def _option_from_argv(argv: list[str], option: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == option and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(option + "="):
            return arg.split("=", 1)[1]
    return None


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/openapi.json"):
        base_url = base_url[: -len("/openapi.json")]
    return base_url.rstrip("/")


def _openapi_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if base_url.endswith("/openapi.json"):
        return base_url
    return urllib.parse.urljoin(base_url + "/", "openapi.json")


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


def _safe_function_name(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_") or "api_command"


def _dedupe_command_name(
    command_name: str,
    used_names: dict[str, OperationCommand],
    operation: OperationCommand,
) -> str:
    """Avoid command collisions within a resource group.

    Prefer the clean generated name. If another operation in the same resource
    group already uses it, generate a deterministic method/path-derived name so
    a later endpoint can never silently replace or misroute an earlier one.
    """

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
    """Resolve local OpenAPI `$ref` pointers; leave unsupported refs untouched."""

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


app = build_app()
