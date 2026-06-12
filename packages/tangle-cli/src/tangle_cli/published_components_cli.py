"""`tangle sdk published-components` command implementation."""

from __future__ import annotations

import json
import pathlib
from typing import Annotated, Any

from cyclopts import App, Parameter

from .args_container import ArgsContainer, ConfigFileError
from .api_transport import DEFAULT_TIMEOUT_SECONDS

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

app = App(
    name="published-components",
    help="Inspect and search published Tangle components from the registry.",
)


def _client_from_options(
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | None = None,
) -> Any:
    """Create the static client used by published-component commands."""

    try:
        from .client import TangleApiClient
    except ModuleNotFoundError as exc:
        if exc.name == "tangle_api":
            raise SystemExit(
                "Native generated Tangle API bindings are required for "
                "published-component commands. Install tangle-cli[native] "
                "or provide a local tangle_api.generated package."
            ) from exc
        raise

    return TangleApiClient(
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header=header,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def _load_args(config: str | None, **kwargs: Any) -> list[ArgsContainer]:
    try:
        return ArgsContainer.load(config, **kwargs)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc


def _optional_path(value: str | pathlib.Path | None) -> pathlib.Path | None:
    return pathlib.Path(value) if value is not None else None


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


def search_components(*args: Any, **kwargs: Any) -> Any:
    from .component_inspector import search_components as _search_components

    return _search_components(*args, **kwargs)


def inspect_by_digest(*args: Any, **kwargs: Any) -> Any:
    from .component_inspector import inspect_by_digest as _inspect_by_digest

    return _inspect_by_digest(*args, **kwargs)


def inspect_by_name(*args: Any, **kwargs: Any) -> Any:
    from .component_inspector import inspect_by_name as _inspect_by_name

    return _inspect_by_name(*args, **kwargs)


def get_standard_library(*args: Any, **kwargs: Any) -> Any:
    from .component_inspector import get_standard_library as _get_standard_library

    return _get_standard_library(*args, **kwargs)


def ComponentPublisher(*args: Any, **kwargs: Any) -> Any:  # noqa: N802 - class-shaped lazy factory
    from .component_publisher import ComponentPublisher as _ComponentPublisher

    return _ComponentPublisher(*args, **kwargs)


def deprecate_component(*args: Any, **kwargs: Any) -> Any:
    from .component_publisher import deprecate_component as _deprecate_component

    return _deprecate_component(*args, **kwargs)


@app.command(name="search")
def published_components_search(
    name: str | None = None,
    *,
    include_deprecated: bool | None = None,
    published_by: str | None = None,
    digest: str | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Search published component metadata."""

    for args in _load_args(
        config,
        name=(name, None),
        include_deprecated=(include_deprecated, None),
        published_by=(published_by, None),
        digest=(digest, None),
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        client = _client_from_options(
            base_url=args.base_url,
            token=args.token,
            auth_header=args.auth_header,
            header=args.header,
        )
        _print_json(
            search_components(
                client,
                name=args.name,
                include_deprecated=bool(args.include_deprecated),
                published_by=args.published_by,
                digest=args.digest,
            )
        )


@app.command(name="inspect")
def published_components_inspect(
    name: str | None = None,
    *,
    digest: str | None = None,
    all_versions: bool | None = None,
    include_deprecated: bool | None = None,
    follow_deprecated: bool | None = None,
    full_spec: bool | None = None,
    published_by: str | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Inspect a published component by exact name or digest."""

    for args in _load_args(
        config,
        name=(name, None),
        digest=(digest, None),
        all_versions=(all_versions, None),
        include_deprecated=(include_deprecated, None),
        follow_deprecated=(follow_deprecated, None),
        full_spec=(full_spec, None),
        published_by=(published_by, None),
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        if bool(args.name) == bool(args.digest):
            raise SystemExit("Provide exactly one of NAME or --digest DIGEST")

        client = _client_from_options(
            base_url=args.base_url,
            token=args.token,
            auth_header=args.auth_header,
            header=args.header,
        )
        if args.digest:
            result = inspect_by_digest(
                client,
                args.digest,
                full_spec=bool(args.full_spec),
                follow_deprecated=bool(args.follow_deprecated),
            )
        else:
            result = inspect_by_name(
                client,
                args.name or "",
                include_all_versions=bool(args.all_versions),
                include_deprecated=bool(args.include_deprecated),
                full_spec=bool(args.full_spec),
                published_by=args.published_by,
            )
        _print_json(result)


@app.command(name="library")
def published_components_library(
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Print the curated standard component library."""

    for args in _load_args(
        config,
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        client = _client_from_options(
            base_url=args.base_url,
            token=args.token,
            auth_header=args.auth_header,
            header=args.header,
        )
        _print_json(get_standard_library(client))


@app.command(name="publish")
def published_components_publish(
    component_path: pathlib.Path | None = None,
    *,
    image: str | None = None,
    name: str | None = None,
    description: str | None = None,
    annotations: Annotated[
        str | None,
        Parameter(help="Custom annotations as a JSON object."),
    ] = None,
    dry_run: bool | None = None,
    git_remote_sha: str | None = None,
    git_remote_branch: str | None = None,
    git_remote_url: str | None = None,
    git_root: pathlib.Path | None = None,
    published_by: str | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Publish one component YAML file to a Tangle component registry."""

    all_args = _load_args(
        config,
        component_path=("component_path", component_path, None, False, True, _optional_path),
        image=(image, None),
        name=(name, None),
        description=(description, None),
        annotations=("annotations", annotations, None, True),
        dry_run=(dry_run, None),
        git_remote_sha=(git_remote_sha, None),
        git_remote_branch=(git_remote_branch, None),
        git_remote_url=(git_remote_url, None),
        git_root=(git_root, None, _optional_path),
        published_by=(published_by, None),
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    )
    first_args = all_args[0]
    client = None if first_args.dry_run else _client_from_options(
        base_url=first_args.base_url,
        token=first_args.token,
        auth_header=first_args.auth_header,
        header=first_args.header,
    )
    publisher = ComponentPublisher(
        dry_run=bool(first_args.dry_run),
        git_remote_sha=first_args.git_remote_sha,
        git_remote_branch=first_args.git_remote_branch,
        git_remote_url=first_args.git_remote_url,
        git_root=first_args.git_root,
        published_by=first_args.published_by,
        client=client,
    )
    component_configs = [
        {
            "component_path": args.component_path,
            "image": args.image,
            "name": args.name,
            "description": args.description,
            "annotations": args.annotations,
        }
        for args in all_args
    ]
    exit_code = publisher.publish_components(component_configs)
    results = [
        {"component_path": path, **result.to_dict()}
        for path, result in getattr(publisher, "results", [])
    ]
    error_count = sum(1 for result in results if result.get("status") == "error")
    summary = {
        "status": "failed" if exit_code else "success",
        "components_count": len(results),
        "error_count": error_count,
        "results": results,
    }
    _print_json(summary)
    if exit_code != 0:
        raise SystemExit(exit_code)


@app.command(name="deprecate")
def published_components_deprecate(
    digest: str | None = None,
    *,
    superseded_by: str | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Deprecate a published component by digest."""

    for args in _load_args(
        config,
        digest=("digest", digest, None, False, True),
        superseded_by=(superseded_by, None),
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        client = _client_from_options(
            base_url=args.base_url,
            token=args.token,
            auth_header=args.auth_header,
            header=args.header,
        )
        result = deprecate_component(
            client,
            args.digest,
            superseded_by=args.superseded_by,
        )
        result_dict = result.to_dict() if hasattr(result, "to_dict") else result
        _print_json(result_dict)
        if isinstance(result_dict, dict) and not result_dict.get("success", result_dict.get("status") != "failed"):
            raise SystemExit(1)
