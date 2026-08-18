"""OpenAPI-backed `tangle api` command implementation.

The backend exposes a FastAPI OpenAPI schema. Schema cache, operation naming,
parameter mapping, and HTTP dispatch live in reusable modules so the CLI and
programmatic client share one behavior. Static commands are registered from the
checked-in OpenAPI snapshot, while `refresh` can update the dynamic schema cache
for expansion against a live backend. Commands are generated only when the root
CLI is being built for an actual `tangle api ...` invocation, so importing this
module never reads ambient argv, touches the schema cache, or contacts the
backend.
"""

from __future__ import annotations

import inspect
import json
import os
import re
import sys
from typing import Annotated, Any

import httpx
import platformdirs
from cyclopts import App, Parameter

from .args_container import ArgsContainer, ConfigFileError
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
    DEFAULT_TIMEOUT_SECONDS,
    _ambient_auth_env_present,
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
    describe_request_error,
    format_http_status_error,
    format_request_error,
    http_status_line,
    request_operation,
    sanitize_url,
)
from .cli_helpers import api_arg_specs, load_args_or_exit
from .cli_options import (
    AuthHeaderOption,
    BaseUrlOption,
    ConfigOption,
    HeaderOption,
    TokenOption,
)
from .openapi.parser import load_openapi_schema as load_bundled_openapi_schema

BodyOption = Annotated[
    str | None,
    Parameter(help="JSON request body, or @path/to/file.json."),
]
SchemaSourceOption = Annotated[
    str,
    Parameter(
        help=(
            "OpenAPI schema source for generated API commands: 'auto' merges "
            "checked-in official operations with cached backend extensions "
            "(default); 'official' uses only the checked-in static schema; "
            "'cache' uses only a schema previously written by `tangle api refresh`."
        )
    ),
]


def build_app(schema: dict[str, Any] | None = None) -> App:
    """Build the `tangle api` Cyclopts app.

    When *schema* is supplied, commands are generated from it. Otherwise the
    checked-in official OpenAPI snapshot is always used, and cached live backend
    operations are merged in as dynamic extensions by default. Official
    definitions win for matching method/path operations.
    """

    api_app = App(
        name="api",
        help="Call Tangle backend API endpoints from the checked-in OpenAPI schema.",
    )
    _register_refresh_command(api_app)
    _register_reset_cache_command(api_app)
    _register_schema_source_option(api_app)

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
            _register_schema_source_option(group)
            groups[operation.group_name] = group
            api_app.command(group)

        command = _make_operation_callable(operation)
        group.command(command, name=operation.command_name)


def _register_schema_source_option(app: App) -> None:
    @app.default
    def schema_source_option(*, schema_source: SchemaSourceOption = "auto") -> None:
        """Select merged, official-only, or raw cached backend schema."""

        _validate_schema_source(schema_source)


def _register_refresh_command(api_app: App) -> None:
    @api_app.command(name="refresh")
    def refresh(
        *,
        base_url: BaseUrlOption = None,
        token: TokenOption = None,
        auth_header: AuthHeaderOption = None,
        header: HeaderOption = None,
        config: ConfigOption = None,
    ) -> None:
        """Fetch /openapi.json and update the local schema cache."""

        for args in load_args_or_exit(
            config,
            **api_arg_specs(
                base_url=base_url,
                token=token,
                auth_header=auth_header,
                header=header,
            ),
        ):
            base_url_from_config = base_url is None and "base_url" in args._config
            normalized_base_url = (
                _normalize_base_url(args.base_url) if args.base_url else default_base_url()
            )
            try:
                schema, path = refresh_schema(
                    normalized_base_url,
                    args.token,
                    args.header,
                    args.auth_header,
                    include_env_credentials=not base_url_from_config,
                )
            except httpx.HTTPStatusError as exc:
                # Never echo the /openapi.json response body: an auth failure can
                # reflect the credentials we just sent. Status line only.
                raise SystemExit(_schema_fetch_error_message(normalized_base_url, exc)) from exc
            except httpx.RequestError as exc:
                raise SystemExit(_schema_fetch_error_message(normalized_base_url, exc)) from exc
            path_count = len(schema.get("paths", {}))
            print(f"Cached OpenAPI schema for {normalized_base_url}")
            print(f"Path: {path}")
            print(f"OpenAPI paths: {path_count}")


def _register_reset_cache_command(api_app: App) -> None:
    @api_app.command(name="reset-cache")
    def reset_cache(*, base_url: BaseUrlOption = None, config: ConfigOption = None) -> None:
        """Delete the cached live OpenAPI schema for a base URL."""

        for args in load_args_or_exit(config, base_url=(base_url, None)):
            normalized_base_url = (
                _normalize_base_url(args.base_url) if args.base_url else default_base_url()
            )
            path = cache_path(normalized_base_url)
            if path.exists():
                path.unlink()
                print(f"Deleted cached OpenAPI schema for {normalized_base_url}")
                print(f"Path: {path}")
            else:
                print(f"No cached OpenAPI schema for {normalized_base_url}")
                print(f"Path: {path}")


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
                default=None,
                annotation=_optional_type(annotation),
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
        default = parameter.default if not parameter.required else None
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
            "config",
            inspect.Parameter.KEYWORD_ONLY,
            default=None,
            annotation=ConfigOption,
        )
    )
    parameters.append(
        inspect.Parameter(
            "schema_source",
            inspect.Parameter.KEYWORD_ONLY,
            default="auto",
            annotation=SchemaSourceOption,
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

    config = values.pop("config", None)
    cli_body = values.get("body") if operation.has_request_body else None
    cli_base_url = values.get("base_url")
    for args in _operation_args_from_config(operation, values, config):
        body_from_config = operation.has_request_body and cli_body is None and "body" in args._config
        base_url_from_config = cli_base_url is None and "base_url" in args._config
        _invoke_operation_once(
            operation,
            args.to_dict(),
            allow_body_file_references=not body_from_config,
            include_env_credentials=not base_url_from_config,
        )


def _operation_args_from_config(
    operation: OperationCommand,
    values: dict[str, Any],
    config: str | None,
) -> list[ArgsContainer]:
    specs: dict[str, tuple[Any, ...]] = {}
    for parameter in operation.parameters:
        default = parameter.default if not parameter.required else None
        required = parameter.required and parameter.location != "body"
        specs[parameter.local_name] = (
            parameter.local_name,
            values.get(parameter.local_name, default),
            default,
            False,
            required,
        )

    specs["schema_source"] = (values.get("schema_source", "auto"), "auto")
    if operation.has_request_body:
        specs["body"] = (values.get("body"), None)
    specs.update(
        api_arg_specs(
            base_url=values.get("base_url"),
            token=values.get("token"),
            auth_header=values.get("auth_header"),
            header=values.get("header"),
        )
    )
    resolved = load_args_or_exit(config, **specs)
    for args in resolved:
        for parameter in operation.parameters:
            if parameter.required or parameter.default is None:
                continue
            if parameter.local_name in args._config:
                continue
            if getattr(args, parameter.local_name, None) == parameter.default:
                setattr(args, parameter.local_name, None)
    return resolved


def _invoke_operation_once(
    operation: OperationCommand,
    values: dict[str, Any],
    *,
    allow_body_file_references: bool = True,
    include_env_credentials: bool = True,
) -> None:
    _validate_schema_source(values.pop("schema_source", "official"))
    base_url = _normalize_base_url(values.pop("base_url", None) or default_base_url())
    token = values.pop("token", None)
    if token is None and include_env_credentials:
        token = default_token()
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
            allow_body_file_references=allow_body_file_references,
            include_env_credentials=include_env_credentials,
        )
    except httpx.HTTPStatusError as exc:
        # One-line, credential-safe failure with a non-zero exit. The prior code
        # raised the HTTP status as the exit code, but exit codes are 8-bit, so a
        # status was truncated (404 -> 148, 500 -> 244) and multiples of 256
        # reported success. A string exit prints to stderr and exits 1.
        raise SystemExit(format_http_status_error(exc)) from exc
    except httpx.RequestError as exc:
        raise SystemExit(format_request_error(exc)) from exc
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
    """Return schema needed to build API commands for this process.

    Static commands come from the checked-in official OpenAPI snapshot and are
    available on a cold cache. By default, cached live backend operations are
    merged in as extensions without overriding official operations.
    """

    api_tail = _api_argv_tail(sys.argv)
    if api_tail is None:
        return None

    first_command = _api_first_command(api_tail)
    if first_command in {"refresh", "reset-cache"}:
        return None
    help_requested = _api_tail_requests_help(api_tail)

    schema_source = _schema_source_from_argv(api_tail)
    base_url_arg, base_url_source = _base_url_with_source_from_argv(api_tail)
    configured_base_url = base_url_arg or os.environ.get("TANGLE_API_URL")
    include_env_credentials = base_url_source != "config"
    token = _token_from_argv(api_tail, include_env_credentials=include_env_credentials)
    auth_header = _auth_header_from_argv(api_tail, include_env_credentials=include_env_credentials)
    header = _headers_from_argv(api_tail)
    if schema_source == "cache":
        base_url = configured_base_url or default_base_url()
        cached = load_cached_schema(base_url)
        if cached is None:
            raise SystemExit(
                f"No cached OpenAPI schema for {_normalize_base_url(base_url)}. "
                "Run `tangle api refresh` with the same --base-url/--auth-header/--header options, "
                "or use a tangle-cli environment with an official or custom tangle-api package "
                "that provides tangle_api.schema."
            )
        return cached

    cache_base_url = _auto_cache_base_url(configured_base_url, help_requested)
    cached = load_cached_schema(cache_base_url) if cache_base_url else None
    try:
        official = load_bundled_openapi_schema()
    except FileNotFoundError as exc:
        if first_command is None:
            return None
        if schema_source == "auto" and cache_base_url:
            try:
                return load_or_fetch_schema(
                    cache_base_url,
                    token=token,
                    header=header,
                    auth_header=auth_header,
                    include_env_credentials=include_env_credentials,
                )
            except (httpx.HTTPError, RuntimeError, ValueError, json.JSONDecodeError) as fetch_exc:
                raise SystemExit(_missing_official_schema_message()) from fetch_exc
        raise SystemExit(_missing_official_schema_message()) from exc

    if schema_source == "official":
        return official
    if cached is None:
        return official
    return _merge_official_with_cached_extensions(official, cached)


def _auto_cache_base_url(
    configured_base_url: str | None,
    help_requested: bool,
) -> str | None:
    if configured_base_url:
        return configured_base_url
    if help_requested and _ambient_auth_env_present() and not os.environ.get("TANGLE_API_URL"):
        return None
    return default_base_url()


def _api_tail_requests_help(api_tail: list[str]) -> bool:
    skip_next = False
    options_with_values = {
        "--base-url",
        "--api-url",
        "--token",
        "--auth-header",
        "--header",
        "-H",
        "--schema-source",
        "--config",
    }
    for arg in api_tail:
        if skip_next:
            skip_next = False
            continue
        if arg in options_with_values:
            skip_next = True
            continue
        if arg in {"--help", "-h"}:
            return True
    return False


def _missing_official_schema_message() -> str:
    return (
        "Official static Tangle API commands require a tangle-api package "
        "because the OpenAPI snapshot lives in tangle_api.schema. Normal "
        "tangle-cli installs include the official package; custom generated "
        "API projects should run with a local src/tangle_api package that "
        "shadows site-packages or install a compatible private tangle-api "
        "distribution. Otherwise run `tangle api refresh` and use "
        "`--schema-source cache` for cached backend operations."
    )


def _merge_official_with_cached_extensions(
    official: dict[str, Any],
    cached: dict[str, Any],
) -> dict[str, Any]:
    """Return official schema plus cached-only extension operations.

    Official operations win for matching method/path pairs. Cached schemas can
    contribute entirely new paths, additional methods on existing paths, and
    component definitions needed by cached-only extension operations.
    """

    merged = json.loads(json.dumps(official))
    cached_paths = cached.get("paths", {}) or {}
    merged_paths = merged.setdefault("paths", {})
    for path, cached_path_item in cached_paths.items():
        if not isinstance(cached_path_item, dict):
            continue
        if path not in merged_paths or not isinstance(merged_paths[path], dict):
            merged_paths[path] = json.loads(json.dumps(cached_path_item))
            continue
        merged_path_item = merged_paths[path]
        for key, value in cached_path_item.items():
            if key.lower() in SUPPORTED_METHODS:
                # Preserve official operation definitions when method/path match.
                merged_path_item.setdefault(key, json.loads(json.dumps(value)))
            elif key not in merged_path_item:
                # Preserve cached-only path-level metadata for cached-only methods.
                merged_path_item[key] = json.loads(json.dumps(value))

    _merge_missing_dict_keys(merged.setdefault("components", {}), cached.get("components", {}) or {})
    return merged


def _merge_missing_dict_keys(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key not in target:
            target[key] = json.loads(json.dumps(value))
        elif isinstance(target[key], dict) and isinstance(value, dict):
            _merge_missing_dict_keys(target[key], value)


def _argv_requests_api_schema(argv: list[str]) -> bool:
    api_tail = _api_argv_tail(argv)
    if api_tail is None:
        return False
    first_command = _api_first_command(api_tail)
    return first_command not in {None, "refresh", "reset-cache"}


def _argv_dispatches_dynamic_command(argv: list[str]) -> bool:
    api_tail = _api_argv_tail(argv)
    if api_tail is None:
        return False
    first_command = _api_first_command(api_tail)
    return first_command not in {None, "refresh", "reset-cache"}


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
    options_with_values = {
        "--base-url",
        "--api-url",
        "--token",
        "--auth-header",
        "--header",
        "-H",
        "--schema-source",
        "--config",
    }
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


def _schema_source_from_argv(argv: list[str]) -> str:
    value = _option_from_argv(argv, "--schema-source")
    if value is None:
        value = _config_value_from_argv(argv, "schema_source")
    return _validate_schema_source(str(value or "auto"))


def _validate_schema_source(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"auto", "official", "cache"}:
        raise SystemExit("--schema-source must be 'auto', 'official', or 'cache'")
    return normalized


def _schema_fetch_error_message(base_url: str, exc: Exception) -> str:
    """One-line, credential-safe failure for an /openapi.json fetch.

    The response body is deliberately omitted for HTTP status errors so an auth
    failure cannot reflect the credentials that were just sent.
    """

    target = sanitize_url(_openapi_url(base_url))
    if isinstance(exc, httpx.HTTPStatusError):
        reason = http_status_line(exc)
    elif isinstance(exc, httpx.RequestError):
        reason = describe_request_error(exc)
    else:
        reason = exc.__class__.__name__
    return f"Failed to fetch {target}: {reason}"


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
    value, _source = _base_url_with_source_from_argv(argv)
    return value


def _base_url_with_source_from_argv(argv: list[str]) -> tuple[str | None, str | None]:
    cli_value = _option_from_argv(argv, "--base-url") or _option_from_argv(argv, "--api-url")
    if cli_value is not None:
        return cli_value, "cli"
    config_value = _optional_str(_config_value_from_argv(argv, "base_url"))
    if config_value is not None:
        return config_value, "config"
    return None, None


def _token_from_argv(argv: list[str], *, include_env_credentials: bool = True) -> str | None:
    token = _option_from_argv(argv, "--token") or _optional_str(_config_value_from_argv(argv, "token"))
    if token is None and include_env_credentials:
        token = default_token()
    return token


def _auth_header_from_argv(argv: list[str], *, include_env_credentials: bool = True) -> str | None:
    auth_header = _option_from_argv(argv, "--auth-header") or _optional_str(
        _config_value_from_argv(argv, "auth_header")
    )
    if auth_header is None and include_env_credentials:
        auth_header = default_auth_header()
    return auth_header


def _config_value_from_argv(argv: list[str], key: str) -> Any:
    config_path = _option_from_argv(argv, "--config")
    if config_path is None:
        return None
    try:
        configs = ArgsContainer._load_config_file(config_path)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    if not configs:
        return None
    return configs[0].get(key)


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _headers_from_argv(argv: list[str]) -> list[str]:
    entries: list[str] = []
    for index, arg in enumerate(argv):
        if arg in {"--header", "-H"} and index + 1 < len(argv):
            entries.append(argv[index + 1])
        elif arg.startswith("--header="):
            entries.append(arg.split("=", 1)[1])
    if entries:
        return entries

    config_header = _config_value_from_argv(argv, "header")
    if isinstance(config_header, list):
        return [str(entry) for entry in config_header]
    if isinstance(config_header, str):
        return [config_header]
    return []


def _option_from_argv(argv: list[str], option: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == option and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith(option + "="):
            return arg.split("=", 1)[1]
    return None


def _safe_function_name(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_") or "api_command"
