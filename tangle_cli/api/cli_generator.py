"""Generate ``tangle api`` CLI commands from the real FastAPI backend.

The backend's routes are introspected (path, HTTP method, handler signature)
and turned into a tree of :mod:`cyclopts` commands that call the API server:

* The first non-parameter URL path segment becomes the command **group**
  (e.g. ``/api/pipeline_runs/{id}`` -> ``pipeline-runs``).
* The route's handler name becomes the **command**
  (e.g. ``get_signed_artifact_url`` -> ``get-signed-artifact-url``).
* Handler parameters are classified by FastAPI itself
  (``route.dependant``) into URL-path, query-string and request-body
  parameters.  Parameters injected via ``fastapi.Depends`` (database session,
  current user, permission checks, ...) are *not* part of the public API and
  are skipped automatically.
* Primitive parameters stay primitive on the CLI.  Parameters with a complex
  type (Pydantic model, dataclass, list, dict, ...) accept a JSON value or a
  ``@path`` to a JSON/YAML file, and are validated against the declared type
  on a best-effort basis.

Every generated command also accepts ``--debug``, which prints the HTTP
request that would be sent (method, URL and body) instead of sending it.
"""

import dataclasses
import datetime
import enum
import inspect
import json
import pathlib
import sys
import types
import typing
import urllib.parse
import uuid
from typing import Annotated, Any, Optional

import cyclopts
import fastapi
import fastapi.routing

from . import get_client

_SCALAR_TYPES: tuple[type, ...] = (
    str,
    int,
    float,
    bool,
    bytes,
    datetime.datetime,
    datetime.date,
    datetime.time,
    uuid.UUID,
)

_COMPLEX_ARG_HELP = "JSON value, or @path to a JSON/YAML file."


# --------------------------------------------------------------------------- #
# Type helpers
# --------------------------------------------------------------------------- #
def _strip_optional(annotation: Any) -> Any:
    """Return ``annotation`` with a single trailing ``None`` member removed."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return typing.Union[tuple(args)]  # noqa: UP007 - dynamic union
    return annotation


def _is_scalar(annotation: Any) -> bool:
    core = _strip_optional(annotation)
    return isinstance(core, type) and issubclass(core, _SCALAR_TYPES)


def _jsonable(value: Any) -> Any:
    """Convert a scalar into something ``json.dumps`` can handle."""
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return value


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, enum.Enum):
        return str(value.value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    return str(value)


def _kebab(name: str) -> str:
    return name.replace("_", "-").strip("-")


def _resource_group(path: str) -> str:
    """The first static (non-templated) path segment after an ``api`` prefix."""
    segments = [s for s in path.strip("/").split("/") if s]
    if segments and segments[0] == "api":
        segments = segments[1:]
    for segment in segments:
        if not (segment.startswith("{") and segment.endswith("}")):
            return _kebab(segment)
    return "default"


# --------------------------------------------------------------------------- #
# Route plan
# --------------------------------------------------------------------------- #
@dataclasses.dataclass
class _BodyParam:
    name: str
    is_scalar: bool
    required: bool
    default: Any
    core_type: Any


@dataclasses.dataclass
class _QueryParam:
    name: str
    default: Any


@dataclasses.dataclass
class _RoutePlan:
    method: str
    path: str
    path_params: list[str]
    query_params: list[_QueryParam]
    body_params: list[_BodyParam]
    body_is_direct: bool


def _primary_method(route: fastapi.routing.APIRoute) -> str | None:
    methods = sorted((route.methods or set()) - {"HEAD", "OPTIONS"})
    return methods[0] if methods else None


def _build_plan_and_signature(
    route: fastapi.routing.APIRoute, method: str
) -> tuple[_RoutePlan, inspect.Signature]:
    dependant = route.dependant
    parameters: list[inspect.Parameter] = []
    empty = inspect.Parameter.empty

    # URL path parameters -> required positional arguments.
    path_param_names: list[str] = []
    for field in dependant.path_params:
        path_param_names.append(field.name)
        parameters.append(
            inspect.Parameter(
                field.name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=field.field_info.annotation,
            )
        )

    # Query-string parameters -> keyword options (native types).
    query_params: list[_QueryParam] = []
    for field in dependant.query_params:
        required = field.field_info.is_required()
        default = empty if required else field.field_info.default
        query_params.append(_QueryParam(name=field.name, default=field.field_info.default))
        parameters.append(
            inspect.Parameter(
                field.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=field.field_info.annotation,
            )
        )

    # Request-body parameters -> keyword options (JSON/@file for complex types).
    body_params: list[_BodyParam] = []
    for field in dependant.body_params:
        annotation = field.field_info.annotation
        required = field.field_info.is_required()
        is_scalar = _is_scalar(annotation)
        body_params.append(
            _BodyParam(
                name=field.name,
                is_scalar=is_scalar,
                required=required,
                default=field.field_info.default,
                core_type=_strip_optional(annotation),
            )
        )
        if is_scalar:
            cli_annotation = annotation
            default = empty if required else field.field_info.default
        else:
            inner = str if required else Optional[str]
            cli_annotation = Annotated[inner, cyclopts.Parameter(help=_COMPLEX_ARG_HELP)]
            default = empty if required else None
        parameters.append(
            inspect.Parameter(
                field.name,
                inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=cli_annotation,
            )
        )

    # A single body parameter is sent as the whole body only when FastAPI did
    # not wrap it in a generated ``Body_...`` model (i.e. no ``embed=True`` and
    # not part of a multi-parameter body).
    body_field = route.body_field
    body_is_direct = bool(
        body_field is not None
        and len(body_params) == 1
        and body_field.name == body_params[0].name
    )

    if "debug" not in {p.name for p in parameters}:
        parameters.append(
            inspect.Parameter(
                "debug",
                inspect.Parameter.KEYWORD_ONLY,
                default=False,
                annotation=Annotated[
                    bool,
                    cyclopts.Parameter(
                        help="Print the HTTP request (method, URL, body) instead of sending it."
                    ),
                ],
            )
        )

    plan = _RoutePlan(
        method=method,
        path=route.path,
        path_params=path_param_names,
        query_params=query_params,
        body_params=body_params,
        body_is_direct=body_is_direct,
    )
    return plan, inspect.Signature(parameters)


# --------------------------------------------------------------------------- #
# Argument -> request translation
# --------------------------------------------------------------------------- #
def _load_structured(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        import yaml  # Lazy import; only needed for complex arguments.

        return yaml.safe_load(text)


def _parse_complex_arg(raw: str, spec: _BodyParam) -> Any:
    text = raw
    if raw.startswith("@"):
        text = pathlib.Path(raw[1:]).read_text(encoding="utf-8")
    value = _load_structured(text)

    # Best-effort validation against the declared model.
    model = spec.core_type
    validator = getattr(model, "from_json_dict", None) or getattr(model, "model_validate", None)
    if validator is not None:
        try:
            validator(value)
        except Exception as exc:  # noqa: BLE001 - validation is advisory only.
            cli_name = "--" + _kebab(spec.name)
            print(f"Warning: {cli_name} failed validation against {getattr(model, '__name__', model)}: {exc}",
                  file=sys.stderr)
    return value


def _build_path(plan: _RoutePlan, values: dict[str, Any]) -> str:
    path = plan.path
    for name in plan.path_params:
        encoded = urllib.parse.quote(str(values[name]), safe="")
        path = path.replace("{" + name + "}", encoded)
    return path


def _build_query(plan: _RoutePlan, values: dict[str, Any]) -> list[tuple[str, str]] | None:
    params: list[tuple[str, str]] = []
    for spec in plan.query_params:
        value = values.get(spec.name)
        # Omit unset values and values left at their default: the server
        # applies the same default, and it keeps the request URL clean.
        if value is None or value == spec.default:
            continue
        if isinstance(value, (list, tuple, set)):
            params.extend((spec.name, _query_value(item)) for item in value)
        else:
            params.append((spec.name, _query_value(value)))
    return params or None


def _build_body(plan: _RoutePlan, values: dict[str, Any]) -> Any:
    if not plan.body_params:
        return None
    assembled: dict[str, Any] = {}
    for spec in plan.body_params:
        value = values.get(spec.name)
        if value is None and not spec.required:
            continue
        if spec.is_scalar:
            assembled[spec.name] = _jsonable(value)
        else:
            assembled[spec.name] = _parse_complex_arg(value, spec)
    if plan.body_is_direct:
        return next(iter(assembled.values())) if assembled else None
    return assembled


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def _emit_request(request) -> None:
    print(f"{request.method} {request.url}")
    if request.content:
        print(request.content.decode("utf-8", errors="replace"))


def _emit_response(response) -> None:
    content_type = response.headers.get("content-type", "")
    text = response.text
    if "application/json" in content_type and text:
        try:
            text = json.dumps(response.json(), indent=2, ensure_ascii=False)
        except ValueError:
            pass
    if response.is_success:
        if text:
            print(text)
        return
    print(f"HTTP {response.status_code} {response.reason_phrase}", file=sys.stderr)
    if text:
        print(text, file=sys.stderr)
    raise SystemExit(1)


# --------------------------------------------------------------------------- #
# Command factory
# --------------------------------------------------------------------------- #
def _make_command(plan: _RoutePlan, signature: inspect.Signature, doc: str | None):
    def command(*args: Any, **kwargs: Any) -> None:
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        debug = values.pop("debug", False)

        path = _build_path(plan, values)
        query = _build_query(plan, values)
        body = _build_body(plan, values)

        httpx_client = get_client().get_httpx_client()
        request = httpx_client.build_request(
            plan.method,
            path,
            params=query,
            json=body if body is not None else None,
        )

        if debug:
            _emit_request(request)
            return

        import httpx  # Lazy import; only needed when actually sending.

        try:
            response = httpx_client.send(request)
        except httpx.HTTPError as exc:
            print(f"Request to {request.url} failed: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        _emit_response(response)

    command.__signature__ = signature  # type: ignore[attr-defined]
    command.__name__ = _build_python_name(plan)
    command.__doc__ = doc
    return command


def _build_python_name(plan: _RoutePlan) -> str:
    return f"{plan.method.lower()}_{plan.path.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"


# --------------------------------------------------------------------------- #
# Public entry points
# --------------------------------------------------------------------------- #
def build_backend_app() -> fastapi.FastAPI:
    """Build the FastAPI app from the backend for route introspection."""
    from cloud_pipelines_backend import api_router

    app = fastapi.FastAPI()
    api_router.setup_routes(
        app=app,
        db_engine=None,
        user_details_getter=lambda *args, **kwargs: None,
    )
    return app


def build_api_cli_app(
    name: str = "api", fastapi_app: fastapi.FastAPI | None = None
) -> cyclopts.App:
    """Build a cyclopts app whose commands call the backend's HTTP API."""
    if fastapi_app is None:
        fastapi_app = build_backend_app()

    app = cyclopts.App(name=name, help="Call the Tangle API server.")
    groups: dict[str, cyclopts.App] = {}
    registered: dict[tuple[str, str], Any] = {}

    # Counter so that CLI command order matches the API route order.
    counter = 0
    routes = fastapi_app.routes
    for route in routes:
        if not isinstance(route, fastapi.routing.APIRoute):
            continue
        method = _primary_method(route)
        if method is None:
            continue

        group_name = _resource_group(route.path)
        command_name = _kebab(route.name)
        key = (group_name, command_name)

        existing = registered.get(key)
        if existing is not None:
            # The same handler is sometimes registered under several URLs
            # (e.g. a deprecated alias). Keep the first; only disambiguate
            # genuinely different handlers.
            if existing is route.endpoint:
                continue
            command_name = f"{command_name}-{method.lower()}"
            key = (group_name, command_name)

        group_app = groups.get(group_name)
        if group_app is None:
            group_app = cyclopts.App(name=group_name)
            groups[group_name] = group_app
            app.command(group_app)

        plan, signature = _build_plan_and_signature(route, method)
        command = _make_command(plan, signature, doc=route.endpoint.__doc__)
        group_app.command(command, name=command_name, sort_key=counter)
        counter += 1
        registered[key] = route.endpoint

    return app
