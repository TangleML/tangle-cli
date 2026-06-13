"""`tangle sdk artifacts` read-only artifact commands."""

from __future__ import annotations

import json
from typing import Annotated, Any

from cyclopts import App, Parameter

from .api_transport import DEFAULT_TIMEOUT_SECONDS
from .args_container import ArgsContainer, ConfigFileError

BaseUrlOption = Annotated[
    str | None,
    Parameter(help="Tangle API base URL. Defaults to TANGLE_API_URL, then localhost."),
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
        help="Custom request header as 'Name: value'. Repeat for multiple.",
        negative_iterable=(),
    ),
]
ConfigOption = Annotated[
    str | None,
    Parameter(help="YAML/JSON config file providing command defaults."),
]
QueryOption = Annotated[
    str | None,
    Parameter(
        name="--query",
        alias="-q",
        help=(
            "JSON query with optional keys: "
            "'tasks', 'components', 'executions', and 'artifact_ids'. "
            "Empty output lists mean all outputs."
        ),
    ),
]

app = App(
    name="artifacts",
    help="Read artifact metadata for Tangle pipeline runs.",
)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_args(config: str | None, **kwargs: Any) -> list[ArgsContainer]:
    try:
        return ArgsContainer.load(config, **kwargs)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc


def _client_from_options(
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | None = None,
    include_env_credentials: bool = True,
) -> Any:
    """Create the static client used by read-only artifact commands."""

    try:
        from .client import TangleApiClient
    except ModuleNotFoundError as exc:
        if exc.name == "tangle_api":
            raise SystemExit(
                "Native generated Tangle API bindings are required for "
                "artifact commands. Install tangle-cli[native] or provide "
                "a local tangle_api.generated package."
            ) from exc
        raise

    return TangleApiClient(
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header=header,
        timeout=DEFAULT_TIMEOUT_SECONDS,
        include_env_credentials=include_env_credentials,
    )


def _include_env_credentials(args: ArgsContainer, cli_base_url: str | None) -> bool:
    config_base_url = getattr(args, "_config", {}).get("base_url")
    return not (cli_base_url is None and config_base_url is not None)


def _api_arg_specs(
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | None = None,
) -> dict[str, tuple[Any, ...]]:
    return {
        "base_url": (base_url, None),
        "token": (token, None),
        "auth_header": (auth_header, None),
        "header": (header, None),
    }


def get_artifacts(*args: Any, **kwargs: Any) -> Any:
    from .artifacts import get_artifacts as _get_artifacts

    return _get_artifacts(*args, **kwargs)


def _serialize_artifacts(*args: Any, **kwargs: Any) -> Any:
    from .artifacts import _serialize_artifacts as _serialize

    return _serialize(*args, **kwargs)


@app.command(name="get")
def artifacts_get(
    run_id: str | None = None,
    *,
    query: QueryOption = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Get artifact metadata for tasks/components in a pipeline run."""

    all_args = _load_args(
        config,
        run_id=("run_id", run_id, None, False, True),
        query=("query", query, None, True, True),
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    )

    results: list[dict[str, Any]] = []
    for args in all_args:
        client = _client_from_options(
            base_url=args.base_url,
            token=args.token,
            auth_header=args.auth_header,
            header=args.header,
            include_env_credentials=_include_env_credentials(args, base_url),
        )
        try:
            artifacts = get_artifacts(args.run_id, args.query, client=client)
        except RuntimeError as exc:
            _print_json({"status": "error", "error": str(exc)})
            raise SystemExit(1) from exc

        results.append(
            {
                "status": "success",
                "run_id": args.run_id,
                "count": len(artifacts),
                "artifacts": _serialize_artifacts(artifacts),
            }
        )

    _print_json(results[0] if len(results) == 1 else {"status": "success", "results": results})
