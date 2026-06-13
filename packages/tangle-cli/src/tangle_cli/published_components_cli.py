"""`tangle sdk published-components` command implementation."""

from __future__ import annotations

import pathlib
from typing import Annotated, Any

from cyclopts import App, Parameter

from .cli_helpers import (
    api_arg_specs,
    LazyTangleApiClient,
    include_env_credentials_for_args,
    load_args_or_exit,
    optional_path,
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
from .component_publisher import ComponentPublisher, deprecate_component


def _client_from_options(
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | str | None = None,
    include_env_credentials: bool = True,
    command_name: str = "published-component commands",
) -> LazyTangleApiClient:
    """Create a lazy static client proxy for published-component commands.

    Kept as a module-level seam so downstream wrappers/tests can monkeypatch
    client construction without replacing the shared LazyTangleApiClient class.
    """

    return LazyTangleApiClient(
        base_url=base_url,
        token=token,
        auth_header=auth_header,
        header=header,
        include_env_credentials=include_env_credentials,
        command_name=command_name,
    )


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


app = App(
    name="published-components",
    help="Inspect and search published Tangle components from the registry.",
)


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
    log_type: LogTypeOption = "console",
) -> None:
    """Search published component metadata."""

    for args in load_args_or_exit(
        config,
        name=(name, None),
        include_deprecated=(include_deprecated, None),
        published_by=(published_by, None),
        digest=(digest, None),
        log_type=(log_type, "console"),
        **api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        logger, finalize_logs = logger_for_log_type(args.log_type)
        try:
            client = _client_from_options(
                base_url=args.base_url,
                token=args.token,
                auth_header=args.auth_header,
                header=args.header,
                include_env_credentials=include_env_credentials_for_args(args, base_url),
                command_name="published-component commands",
            )
            if require_available := getattr(client, "require_available", None):
                require_available()

            print_json(
                search_components(
                    client,
                    name=args.name,
                    include_deprecated=bool(args.include_deprecated),
                    published_by=args.published_by,
                    digest=args.digest,
                )
            )
        finally:
            finalize_logs()


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
    log_type: LogTypeOption = "console",
) -> None:
    """Inspect a published component by exact name or digest."""

    for args in load_args_or_exit(
        config,
        name=(name, None),
        digest=(digest, None),
        all_versions=(all_versions, None),
        include_deprecated=(include_deprecated, None),
        follow_deprecated=(follow_deprecated, None),
        full_spec=(full_spec, None),
        published_by=(published_by, None),
        log_type=(log_type, "console"),
        **api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        logger, finalize_logs = logger_for_log_type(args.log_type)
        try:
            if bool(args.name) == bool(args.digest):
                raise SystemExit("Provide exactly one of NAME or --digest DIGEST")

            client = _client_from_options(
                base_url=args.base_url,
                token=args.token,
                auth_header=args.auth_header,
                header=args.header,
                include_env_credentials=include_env_credentials_for_args(args, base_url),
                command_name="published-component commands",
            )
            if require_available := getattr(client, "require_available", None):
                require_available()
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
            print_json(result)
        finally:
            finalize_logs()


@app.command(name="library")
def published_components_library(
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Print the curated standard component library."""

    for args in load_args_or_exit(
        config,
        log_type=(log_type, "console"),
        **api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        logger, finalize_logs = logger_for_log_type(args.log_type)
        try:
            client = _client_from_options(
                base_url=args.base_url,
                token=args.token,
                auth_header=args.auth_header,
                header=args.header,
                include_env_credentials=include_env_credentials_for_args(args, base_url),
                command_name="published-component commands",
            )
            if require_available := getattr(client, "require_available", None):
                require_available()

            print_json(get_standard_library(client))
        finally:
            finalize_logs()


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
    log_type: LogTypeOption = "console",
) -> None:
    """Publish one component YAML file to a Tangle component registry."""

    all_args = load_args_or_exit(
        config,
        component_path=("component_path", component_path, None, False, True, optional_path),
        image=(image, None),
        name=(name, None),
        description=(description, None),
        annotations=("annotations", annotations, None, True),
        dry_run=(dry_run, None),
        git_remote_sha=(git_remote_sha, None),
        git_remote_branch=(git_remote_branch, None),
        git_remote_url=(git_remote_url, None),
        git_root=(git_root, None, optional_path),
        published_by=(published_by, None),
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
            client = None if args.dry_run else _client_from_options(
                base_url=args.base_url,
                token=args.token,
                auth_header=args.auth_header,
                header=args.header,
                include_env_credentials=include_env_credentials_for_args(args, base_url),
                command_name="published-component commands",
            )
            publisher = ComponentPublisher(
                dry_run=bool(args.dry_run),
                git_remote_sha=args.git_remote_sha,
                git_remote_branch=args.git_remote_branch,
                git_remote_url=args.git_remote_url,
                git_root=args.git_root,
                published_by=args.published_by,
                client=client,
                logger=logger,
            )
            result = publisher.publish_component(
                args.component_path,
                image=args.image,
                name=args.name,
                description=args.description,
                annotations=args.annotations,
            )
            result_dict = result.to_dict() if hasattr(result, "to_dict") else dict(result)
            results.append({"component_path": str(args.component_path), **result_dict})
        finally:
            finalize_logs()

    error_count = sum(1 for result in results if result.get("status") in {"error", "failed"})
    summary = {
        "status": "failed" if error_count else "success",
        "components_count": len(results),
        "error_count": error_count,
        "results": results,
    }
    print_json(summary)
    if error_count:
        raise SystemExit(1)


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
    log_type: LogTypeOption = "console",
) -> None:
    """Deprecate a published component by digest."""

    for args in load_args_or_exit(
        config,
        digest=("digest", digest, None, False, True),
        superseded_by=(superseded_by, None),
        log_type=(log_type, "console"),
        **api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        logger, finalize_logs = logger_for_log_type(args.log_type)
        try:
            client = _client_from_options(
                base_url=args.base_url,
                token=args.token,
                auth_header=args.auth_header,
                header=args.header,
                include_env_credentials=include_env_credentials_for_args(args, base_url),
                command_name="published-component commands",
            )
            result = deprecate_component(
                client,
                args.digest,
                superseded_by=args.superseded_by,
                logger=logger,
            )
            result_dict = result.to_dict() if hasattr(result, "to_dict") else result
            print_json(result_dict)
            if isinstance(result_dict, dict) and not result_dict.get("success", result_dict.get("status") != "failed"):
                raise SystemExit(1)
        finally:
            finalize_logs()
