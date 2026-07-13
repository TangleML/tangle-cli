"""`tangle sdk artifacts` artifact metadata, listing, and download commands."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Callable

import requests
from cyclopts import App, Parameter

from .args_container import ConfigFileError
from .cli_helpers import (
    LazyTangleApiClient,
    api_arg_specs,
    include_env_credentials_for_args,
    load_args_or_exit,
    print_json,
)
from .cli_options import (
    AuthHeaderOption,
    BaseUrlOption,
    ConfigOption,
    HeaderOption,
    LogTypeOption,
    TokenOption,
)
from .logger import logger_for_log_type

QueryOption = Annotated[
    str | None,
    Parameter(
        name="--query",
        alias="-q",
        help=(
            "JSON query with optional keys: "
            "'tasks', 'components', 'executions', and 'artifact_ids'. "
            "Empty output lists mean all outputs. "
            "Mutually exclusive with --list and --out-dir."
        ),
    ),
]

ListOption = Annotated[
    bool,
    Parameter(
        name="--list",
        help="List result artifact metadata for the run instead of querying by task/component.",
    ),
]

OutDirOption = Annotated[
    str | None,
    Parameter(
        name="--out-dir",
        help="Fetch result artifact bytes into this directory (created if missing).",
    ),
]

OnlyOption = Annotated[
    list[str] | None,
    Parameter(
        name="--only",
        help="Restrict --out-dir output to these output names (repeatable). Requires --out-dir.",
    ),
]

IncludeChildrenOption = Annotated[
    bool,
    Parameter(
        name="--include-children",
        help="Include direct child task outputs. Valid only with --list or --out-dir.",
    ),
]

app = App(
    name="artifacts",
    help="Resolve, list, and fetch artifacts for Tangle pipeline runs.",
)

_HTTP_ERROR_BODY_LIMIT = 2000


def _format_http_failure(exc: requests.HTTPError) -> str:
    """Render an API HTTP failure as a concise one-line CLI message.

    The status, reason, attempted method/URL, and a trimmed response body are
    what a caller needs to act on. Only authenticated metadata endpoints raise
    ``HTTPError`` through this layer; signed-URL failures are redacted and
    re-raised as ``RuntimeError`` inside ``ArtifactManager`` and never reach
    this formatter, so echoing the URL here cannot leak signed credentials.
    """

    response = exc.response
    if response is None:
        return f"Tangle API request failed: {exc}"
    request = getattr(response, "request", None)
    target = (
        f"{request.method} {request.url}"
        if request is not None and getattr(request, "url", None)
        else (response.url or "Tangle API")
    )
    reason = f" {response.reason}" if response.reason else ""
    summary = f"Tangle API request failed ({response.status_code}{reason}) for {target}"
    body = (response.text or "").strip()
    if not body:
        return summary
    if len(body) > _HTTP_ERROR_BODY_LIMIT:
        body = f"{body[:_HTTP_ERROR_BODY_LIMIT]}... (truncated)"
    return f"{summary}: {body}"


@contextmanager
def _clean_artifact_errors() -> Iterator[None]:
    """Re-raise API HTTP/transport failures as ``RuntimeError`` for CLI output.

    ``ArtifactManager`` lets ``requests`` exceptions from metadata endpoints
    propagate. This boundary converts them into the ``RuntimeError`` the
    command handler reports as a JSON error with a nonzero exit, instead of
    letting a raw traceback reach the interpreter.
    """

    try:
        yield
    except requests.HTTPError as exc:
        raise RuntimeError(_format_http_failure(exc)) from exc
    except requests.RequestException as exc:
        raise RuntimeError(f"Tangle API request failed: {exc}") from exc


def _bool_config_converter(field_name: str) -> Callable[[Any], bool]:
    def convert(value: Any) -> bool:
        if not isinstance(value, bool):
            raise ConfigFileError(
                f"{field_name} must be a boolean (true/false), got {type(value).__name__}"
            )
        return value

    return convert


def _str_list_config_converter(field_name: str) -> Callable[[Any], list[str]]:
    def convert(value: Any) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ConfigFileError(f"{field_name} must be a list of strings")
        return value

    return convert


def _query_json_converter(field_name: str) -> Callable[[Any], Any]:
    """JSON converter that keeps an explicit empty object distinct from absent.

    The shared JSON converter maps ``"{}"``/``"[]"`` to ``None``, which would
    turn an explicit ``--query '{}'`` into "no query passed" and surface the
    misleading "--query is required" error. Here every JSON string is parsed
    as-is, so ``'{}'`` stays an (empty, valid) object query and non-object
    values reach the object-shape check with their real type.
    """

    def convert(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ConfigFileError(f"Invalid JSON for {field_name}: {exc}") from exc
        raise ConfigFileError(
            f"{field_name} must be a dict, list, or JSON string, got {type(value).__name__}"
        )

    return convert


def _path_config_converter(field_name: str) -> Callable[[Any], str]:
    def convert(value: Any) -> str:
        if not isinstance(value, (str, Path)):
            raise ConfigFileError(f"{field_name} must be a filesystem path string")
        return str(value)

    return convert


def _require_single_mode(*, query: Any, list_outputs: bool, out_dir: Any) -> None:
    """Exactly one of --query / --list / --out-dir must be selected."""

    selected = []
    if query is not None:
        selected.append("--query")
    if list_outputs:
        selected.append("--list")
    if out_dir is not None:
        selected.append("--out-dir")
    if len(selected) > 1:
        raise RuntimeError(f"{' / '.join(selected)} are mutually exclusive; pass only one")
    if not selected:
        raise RuntimeError("--query is required unless --list or --out-dir is set")


def _require_flag_modes(
    *, list_outputs: bool, out_dir: Any, only: Any, include_children: bool
) -> None:
    """--only / --include-children are valid only alongside download/list modes."""

    if only and out_dir is None:
        raise RuntimeError("--only is only valid with --out-dir")
    if include_children and not (list_outputs or out_dir is not None):
        raise RuntimeError("--include-children is only valid with --list or --out-dir")


def _require_object_query(query: Any) -> None:
    if query is not None and not isinstance(query, dict):
        raise RuntimeError("--query must be a JSON object")


@app.command(name="get")
def artifacts_get(
    run_id: str | None = None,
    *,
    query: QueryOption = None,
    list_outputs: ListOption = False,
    out_dir: OutDirOption = None,
    only: OnlyOption = None,
    include_children: IncludeChildrenOption = False,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Resolve, list, or fetch artifacts for a pipeline run.

    Selects exactly one mode: ``--query`` (resolve artifact metadata for
    tasks/components/executions/artifact ids), ``--list`` (list result artifact
    metadata), or ``--out-dir`` (fetch output artifact bytes to a directory).

    Downloaded files are named ``<owner>__<output>__<artifact-id-prefix>`` and
    written with owner-only permissions on POSIX (mode ``0o600``; Windows
    ACLs are not adjusted); inline artifact values are
    JSON-encoded and gain a ``.json`` suffix, while streamed and signed-URL
    downloads keep the name as-is.
    """

    all_args = load_args_or_exit(
        config,
        run_id=("run_id", run_id, None, False, True),
        # query is optional here: --list / --out-dir modes do not use it.
        query=("query", query, None, False, False, _query_json_converter("query")),
        list_outputs=("list", list_outputs, False, False, False, _bool_config_converter("list")),
        out_dir=("out_dir", out_dir, None, False, False, _path_config_converter("out_dir")),
        only=("only", only, None, False, False, _str_list_config_converter("only")),
        include_children=(
            "include_children",
            include_children,
            False,
            False,
            False,
            _bool_config_converter("include_children"),
        ),
        log_type=(log_type, "console"),
        **api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    )

    results: list[dict[str, Any]] = []
    for args in all_args:
        logger, finalize_logs = logger_for_log_type(args.log_type)
        try:
            # Mode/shape validation runs before any client construction so an
            # invalid flag combination fails without touching the network.
            try:
                _require_single_mode(
                    query=args.query,
                    list_outputs=args.list_outputs,
                    out_dir=args.out_dir,
                )
                _require_flag_modes(
                    list_outputs=args.list_outputs,
                    out_dir=args.out_dir,
                    only=args.only,
                    include_children=args.include_children,
                )
                _require_object_query(args.query)
            except RuntimeError as exc:
                print_json({"status": "error", "error": str(exc)})
                raise SystemExit(1) from exc

            client = LazyTangleApiClient(
                base_url=args.base_url,
                token=args.token,
                auth_header=args.auth_header,
                header=args.header,
                include_env_credentials=include_env_credentials_for_args(args, base_url),
                command_name="artifact commands",
                logger=logger,
            )
            if require_available := getattr(client, "require_available", None):
                require_available()
            from .artifacts import ArtifactManager

            manager = ArtifactManager(client=client, logger=logger)
            try:
                with _clean_artifact_errors():
                    if args.out_dir is not None:
                        results.append(_download(manager, args))
                    elif args.list_outputs:
                        results.append(_list(manager, args))
                    else:
                        results.append(_query(manager, args))
            except RuntimeError as exc:
                print_json({"status": "error", "error": str(exc)})
                raise SystemExit(1) from exc
        finally:
            finalize_logs()

    print_json(
        results[0] if len(results) == 1 else {"status": "success", "results": results}
    )


def _query(manager: Any, args: Any) -> dict[str, Any]:
    from .artifacts import ArtifactManager

    artifacts = manager.get_artifacts(args.run_id, args.query)
    return {
        "status": "success",
        "run_id": args.run_id,
        "count": len(artifacts),
        "artifacts": ArtifactManager.serialize_artifacts(artifacts),
    }


def _list(manager: Any, args: Any) -> dict[str, Any]:
    rows = manager.list_result_artifacts(
        args.run_id,
        include_children=args.include_children,
    )
    return {
        "status": "success",
        "run_id": args.run_id,
        "count": len(rows),
        "artifacts": rows,
    }


def _download(manager: Any, args: Any) -> dict[str, Any]:
    artifacts = manager.download_result_artifacts(
        args.run_id,
        out_dir=args.out_dir,
        only=args.only,
        include_children=args.include_children,
    )
    return {
        "status": "success",
        "run_id": args.run_id,
        "count": len(artifacts),
        "out_dir": str(Path(args.out_dir).resolve()),
        "artifacts": {key: str(path.resolve()) for key, path in artifacts.items()},
    }
