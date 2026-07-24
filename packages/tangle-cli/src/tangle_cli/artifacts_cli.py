"""`tangle sdk artifacts` read-only artifact commands."""

from __future__ import annotations

from typing import Annotated, Any

from cyclopts import App, Parameter

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
            "Empty output lists mean all outputs."
        ),
    ),
]

app = App(
    name="artifacts",
    help="Read artifact metadata for Tangle pipeline runs.",
)


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
    log_type: LogTypeOption = "console",
) -> None:
    """Get artifact metadata for tasks/components in a pipeline run."""

    all_args = load_args_or_exit(
        config,
        run_id=("run_id", run_id, None, False, True),
        query=("query", query, None, True, True),
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

            manager = ArtifactManager(client=client)
            try:
                artifacts = manager.get_artifacts(args.run_id, args.query)
            except RuntimeError as exc:
                print_json({"status": "error", "error": str(exc)})
                raise SystemExit(1) from exc

            results.append(
                {
                    "status": "success",
                    "run_id": args.run_id,
                    "count": len(artifacts),
                    "artifacts": ArtifactManager.serialize_artifacts(artifacts),
                }
            )
        finally:
            finalize_logs()

    print_json(
        results[0] if len(results) == 1 else {"status": "success", "results": results}
    )
