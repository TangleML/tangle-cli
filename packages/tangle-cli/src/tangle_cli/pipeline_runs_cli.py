"""`tangle sdk pipeline-runs` command implementation."""

from __future__ import annotations

import pathlib
from typing import Annotated, Any

from cyclopts import App, Parameter

from .args_container import ArgsContainer
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
from .logger import Logger, logger_for_log_type
from .pipeline_runs import (
    PipelineRunError,
    PipelineRunHooks,
    PipelineRunManager,
    parse_json_or_key_values,
    parse_key_value_entries,
)

app = App(name="pipeline-runs", help="Submit and inspect Tangle pipeline runs.")
annotations_app = App(name="annotations", help="Work with pipeline-run annotations.")
app.command(annotations_app)


def _manager(args: ArgsContainer, *, cli_base_url: str | None, logger: Logger) -> PipelineRunManager:
    client = LazyTangleApiClient(
        base_url=args.base_url,
        token=args.token,
        auth_header=args.auth_header,
        header=args.header,
        include_env_credentials=include_env_credentials_for_args(args, cli_base_url),
        command_name="pipeline-run commands",
    )
    return PipelineRunManager(client=client, hooks=PipelineRunHooks(logger=logger), logger=logger)


def _run_manager_action(config: str | None, cli_base_url: str | None, specs: dict[str, tuple[Any, ...]], fn):
    for args in load_args_or_exit(config, **specs):
        try:
            logger, finalize_logs = logger_for_log_type(getattr(args, "log_type", "console"))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            try:
                result = fn(_manager(args, cli_base_url=cli_base_url, logger=logger), args)
            except PipelineRunError as exc:
                raise SystemExit(str(exc)) from exc
            if result is not None:
                print_json(result)
        finally:
            finalize_logs()


@app.command(name="submit")
def pipeline_runs_submit(
    pipeline_path: pathlib.Path | None = None,
    *,
    arg: Annotated[
        list[str] | None,
        Parameter(help="Pipeline argument as KEY=VALUE. Repeat for multiple.", negative_iterable=()),
    ] = None,
    args_json: Annotated[str | None, Parameter(help="Pipeline arguments as a JSON object.")] = None,
    annotation: Annotated[
        list[str] | None,
        Parameter(help="Run annotation as KEY=VALUE. Repeat for multiple.", negative_iterable=()),
    ] = None,
    hydrate: Annotated[bool | None, Parameter(help="Hydrate refs before submit.")] = True,
    dry_run: Annotated[
        bool | None,
        Parameter(help="Hydrate and print the submit payload without creating a run."),
    ] = None,
    run_as: Annotated[
        str | None,
        Parameter(help="Downstream extension point; unsupported by the OSS default hooks."),
    ] = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Hydrate and submit a local pipeline YAML file as a run."""

    specs = {
        "pipeline_path": ("pipeline_path", pipeline_path, None, False, True, optional_path),
        "arg": (arg, None),
        "args_json": (args_json, None),
        "annotation": (annotation, None),
        "hydrate": (hydrate, True),
        "dry_run": (dry_run, None),
        "run_as": (run_as, None),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> dict[str, Any]:
        kwargs = {
            "run_args": parse_json_or_key_values(args.args_json, args.arg),
            "annotations": parse_key_value_entries(args.annotation),
            "hydrate": bool(args.hydrate),
            "run_as": args.run_as,
        }
        if args.dry_run:
            return manager.build_submit_body(args.pipeline_path, **kwargs)
        return manager.submit_pipeline(args.pipeline_path, **kwargs)

    _run_manager_action(config, base_url, specs, action)


@app.command(name="details")
def pipeline_runs_details(
    run_id: str | None = None,
    *,
    include_annotations: bool | None = None,
    include_execution_state: bool | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Print run details, including root execution details."""
    specs = {
        "run_id": (run_id,),
        "include_annotations": (include_annotations, None),
        "include_execution_state": (include_execution_state, None),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.get_run_details(
            args.run_id,
            include_annotations=bool(args.include_annotations),
            include_execution_state=bool(args.include_execution_state),
        ),
    )


@app.command(name="status")
def pipeline_runs_status(
    run_id: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Print a pipeline run and derived status summary."""
    specs = {
        "run_id": (run_id,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> dict[str, Any]:
        run = manager.get_run(args.run_id, include_execution_stats=True)
        return {"run": run, "status": manager.status_from_run(run) or "UNKNOWN"}

    _run_manager_action(config, base_url, specs, action)


@app.command(name="graph-state")
def pipeline_runs_graph_state(
    execution_id: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Print graph execution state for an execution id."""
    specs = {
        "execution_id": (execution_id,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(config, base_url, specs, lambda manager, args: manager.graph_state(args.execution_id))


@app.command(name="cancel")
def pipeline_runs_cancel(
    run_id: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Cancel a pipeline run."""
    specs = {
        "run_id": (run_id,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(config, base_url, specs, lambda manager, args: manager.cancel_run(args.run_id))


@app.command(name="wait")
def pipeline_runs_wait(
    run_id: str | None = None,
    *,
    max_wait: float = 600.0,
    poll_interval: float = 10.0,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Poll a run until terminal state or bounded timeout."""
    specs = {
        "run_id": (run_id,),
        "max_wait": (max_wait, 600.0),
        "poll_interval": (poll_interval, 10.0),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.wait_for_completion(
            args.run_id,
            max_wait=float(args.max_wait),
            poll_interval=float(args.poll_interval),
        ),
    )


@app.command(name="logs")
def pipeline_runs_logs(
    execution_id: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Print Tangle API container logs for an execution id."""
    specs = {
        "execution_id": (execution_id,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> object:
        result = manager.logs(args.execution_id)
        if isinstance(result, dict) and isinstance(result.get("log_text"), str):
            print(result["log_text"], end="" if result["log_text"].endswith("\n") else "\n")
            return None
        return result

    _run_manager_action(config, base_url, specs, action)


@app.command(name="search")
def pipeline_runs_search(
    query: str | None = None,
    *,
    filter_query: str | None = None,
    page_token: str | None = None,
    include_pipeline_names: bool | None = None,
    include_execution_stats: bool | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Search/list pipeline runs using the Tangle API filters."""
    specs = {
        "query": ("filter", query, None, False),
        "filter_query": (filter_query, None),
        "page_token": (page_token, None),
        "include_pipeline_names": (include_pipeline_names, None),
        "include_execution_stats": (include_execution_stats, None),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.search_runs(
            filter=args.query,
            filter_query=args.filter_query,
            page_token=args.page_token,
            include_pipeline_names=args.include_pipeline_names,
            include_execution_stats=args.include_execution_stats,
        ),
    )


@app.command(name="export")
def pipeline_runs_export(
    run_id: str | None = None,
    *,
    output: pathlib.Path | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Export a run's root pipeline spec to YAML."""
    specs = {
        "run_id": (run_id,),
        "output": (output, None, optional_path),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> object:
        result = manager.export_run(args.run_id, args.output)
        if args.output is None and "yaml" in result:
            print(result["yaml"], end="" if result["yaml"].endswith("\n") else "\n")
            return None
        return result

    _run_manager_action(config, base_url, specs, action)


@annotations_app.command(name="list")
def pipeline_runs_annotations_list(
    run_id: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    specs = {
        "run_id": (run_id,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(config, base_url, specs, lambda manager, args: manager.annotations_list(args.run_id))


@annotations_app.command(name="set")
def pipeline_runs_annotations_set(
    run_id: str | None = None,
    key: str | None = None,
    value: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    specs = {
        "run_id": (run_id,),
        "key": (key,),
        "value": (value,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.annotations_set(args.run_id, args.key, args.value),
    )


@annotations_app.command(name="delete")
def pipeline_runs_annotations_delete(
    run_id: str | None = None,
    key: str | None = None,
    *,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    specs = {
        "run_id": (run_id,),
        "key": (key,),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_manager_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.annotations_delete(args.run_id, args.key),
    )
