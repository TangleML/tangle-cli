"""OpenAPI-backed `tangle api` command implementation.

The backend exposes a FastAPI OpenAPI schema. Schema cache, operation naming,
parameter mapping, and HTTP dispatch live in reusable modules so the CLI and
programmatic client share one behavior. Commands are generated only when the
root CLI is being built for an actual `tangle api ...` invocation, so importing
this module never reads ambient argv, touches the schema cache, or contacts the
backend.
"""

from __future__ import annotations

import inspect
import json
import re
import sys
from typing import Annotated, Any

import httpx
import platformdirs
from cyclopts import App, Parameter

from .api_schema import (
    SUPPORTED_METHODS,
    CliParameter,
    OperationCommand,
    cache_path,
    default_cache_dir,
    fetch_schema,
    load_cached_schema,
    load_or_fetch_schema,
    operation_commands,
    refresh_schema,
    write_cached_schema,
    _dedupe_command_name,
    _flatten_schema,
    _is_path_param,
    _is_simple_schema,
    _iter_operation_commands,
    _json_request_body_schema,
    _method_sort_key,
    _normalize_name,
    _operation_command_name,
    _operation_group_name,
    _operation_parameters,
    _path_parts,
    _request_body_parameters,
    _resolve_ref,
    _safe_identifier,
    _same_operation,
    _schema_to_python_type,
    _unwrap_nullable_schema,
)
from .api_transport import (
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT_SECONDS,
    _env_header_entries,
    _headers_from_env,
    _load_body_argument,
    _normalize_auth_header,
    _normalize_base_url,
    _openapi_url,
    _parse_header_entries,
    _request_headers,
    _urlencode_query,
    default_auth_header,
    default_base_url,
    default_token,
    request_operation,
)

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


def build_app(schema: dict[str, Any] | None = None) -> App:
    """Build the `tangle api` Cyclopts app.

    When *schema* is supplied, dynamic commands are generated from it. Otherwise
    schema loading is driven by the current top-level CLI invocation: only actual
    `tangle api ...` commands read the cache or fetch `/openapi.json`.
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

    for operation in operation_commands(schema):
        group = groups.get(operation.group_name)
        if group is None:
            group = App(
                name=operation.group_name,
                help=f"Call {operation.group_name} API endpoints.",
            )
            groups[operation.group_name] = group
            api_app.command(group)

        command = _make_operation_callable(operation)
        group.command(command, name=operation.command_name)


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

        normalized_base_url = _normalize_base_url(base_url) if base_url else default_base_url()
        try:
            schema, path = refresh_schema(normalized_base_url, token, header, auth_header)
        except httpx.HTTPStatusError as exc:
            message = f"HTTP {exc.response.status_code} {exc.response.reason_phrase}"
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


def _invoke_operation(operation: OperationCommand, values: dict[str, Any]) -> None:
    """Turn parsed CLI values into an HTTP request and print the response."""

    base_url = _normalize_base_url(values.pop("base_url", None) or default_base_url())
    token = values.pop("token", None) or default_token()
    auth_header = values.pop("auth_header", None)
    header_entries = values.pop("header", None)
    body_arg = values.pop("body", None) if operation.has_request_body else None

    try:
        response = request_operation(
            operation,
            values,
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header_entries=header_entries,
            body=body_arg,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            allow_body_file_references=True,
        )
    except httpx.HTTPStatusError as exc:
        message = exc.response.text or exc.response.reason_phrase
        print(message, file=sys.stderr)
        raise SystemExit(exc.response.status_code) from exc
    except httpx.RequestError as exc:
        raise SystemExit(f"Failed to call {exc.request.url}: {exc}") from exc
    except TypeError as exc:
        raise SystemExit(str(exc)) from exc

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


def _schema_for_current_invocation() -> dict[str, Any] | None:
    """Return schema needed to build dynamic commands for this process.

    We prefer the cache and only fetch on API-focused invocations. If the user
    is dispatching a dynamic command, fetch failures are made actionable instead
    of letting Cyclopts report that only the static `refresh` command exists.
    """

    api_tail = _api_argv_tail(sys.argv)
    if api_tail is None:
        return None

    base_url = _base_url_from_argv(api_tail) or default_base_url()
    cached = load_cached_schema(base_url)
    if cached is not None:
        return cached
    if not _argv_requests_api_schema(sys.argv):
        return None
    try:
        return load_or_fetch_schema(
            base_url,
            _token_from_argv(api_tail),
            _headers_from_argv(api_tail),
            _auth_header_from_argv(api_tail),
        )
    except Exception as exc:
        if _argv_dispatches_dynamic_command(sys.argv):
            raise SystemExit(_schema_fetch_failure_message(base_url, exc)) from exc
        # Keep `tangle api --help` usable if the backend is unavailable; the
        # explicit `refresh` command or an attempted dynamic command reports the
        # concrete fetch failure and next step.
        return None


def _argv_requests_api_schema(argv: list[str]) -> bool:
    api_tail = _api_argv_tail(argv)
    if api_tail is None:
        return False
    first_command = _api_first_command(api_tail)
    return first_command not in {None, "refresh"}


def _argv_dispatches_dynamic_command(argv: list[str]) -> bool:
    api_tail = _api_argv_tail(argv)
    if api_tail is None:
        return False
    first_command = _api_first_command(api_tail)
    return first_command not in {None, "refresh"}


def _api_argv_tail(argv: list[str]) -> list[str] | None:
    """Return args after the root `api` command, or None for non-API invocations."""

    args = list(argv[1:])
    for index, arg in enumerate(args):
        if arg == "--":
            if index + 1 < len(args) and args[index + 1] == "api":
                return args[index + 2 :]
            return None
        if arg in {"--help", "-h", "--version"}:
            return None
        if arg.startswith("-"):
            return None
        return args[index + 1 :] if arg == "api" else None
    return None


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


def _safe_function_name(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_") or "api_command"
