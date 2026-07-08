"""`tangle sdk pipeline-runs` command implementation."""

from __future__ import annotations

import json
import pathlib
from typing import Annotated, Any

from cyclopts import App, Parameter

from .args_container import ArgsContainer
from .cli_helpers import (
    LazyTangleApiClient,
    api_arg_specs,
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
from .pipeline_run_annotations import AnnotationManager
from .pipeline_run_manager import (
    _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
    PipelineRunError,
    PipelineRunHooks,
    PipelineRunManager,
    parse_json_or_key_values,
    parse_key_value_entries,
)
from .pipeline_run_search import normalize_query_input, parse_annotation

app = App(name="pipeline-runs", help="Submit and inspect Tangle pipeline runs.")
annotations_app = App(name="annotations", help="Work with pipeline-run annotations.")
app.command(annotations_app)


def _trusted_hydration_config(args: ArgsContainer) -> dict[str, Any]:
    config = getattr(args, "_config", {}).get("trusted_hydration", {})
    return config if isinstance(config, dict) else {}


def _trusted_sources_for_args(args: ArgsContainer) -> list[str]:
    sources: list[str] = []
    config_sources = _trusted_hydration_config(args).get("trusted_python_sources", [])
    if isinstance(config_sources, str):
        sources.append(config_sources)
    elif isinstance(config_sources, list):
        sources.extend(str(source) for source in config_sources)
    cli_sources = getattr(args, "trusted_source", None)
    if isinstance(cli_sources, str):
        sources.append(cli_sources)
    elif isinstance(cli_sources, list):
        sources.extend(str(source) for source in cli_sources)
    return [source for source in sources if source]


def _allow_all_hydration_for_args(args: ArgsContainer) -> bool:
    if bool(getattr(args, "trusted_hydration_cli", False)):
        return True
    config = _trusted_hydration_config(args)
    return bool(config.get("allow_all", False))


def _api_client(args: ArgsContainer, *, cli_base_url: str | None, command_name: str) -> LazyTangleApiClient:
    return LazyTangleApiClient(
        base_url=args.base_url,
        token=args.token,
        auth_header=args.auth_header,
        header=args.header,
        include_env_credentials=include_env_credentials_for_args(args, cli_base_url),
        command_name=command_name,
    )


def _manager(args: ArgsContainer, *, cli_base_url: str | None, logger: Logger) -> PipelineRunManager:
    return PipelineRunManager(
        client=_api_client(args, cli_base_url=cli_base_url, command_name="pipeline-run commands"),
        hooks=PipelineRunHooks(
            logger=logger,
            trusted_python_sources=_trusted_sources_for_args(args),
            allow_all_hydration=_allow_all_hydration_for_args(args),
        ),
        logger=logger,
    )


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


def _run_annotation_action(config: str | None, cli_base_url: str | None, specs: dict[str, tuple[Any, ...]], fn):
    for args in load_args_or_exit(config, **specs):
        try:
            logger, finalize_logs = logger_for_log_type(getattr(args, "log_type", "console"))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        try:
            manager = AnnotationManager(
                client=_api_client(args, cli_base_url=cli_base_url, command_name="pipeline-run annotation commands"),
                logger=logger,
            )
            print_json(fn(manager, args))
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
    trusted_source: Annotated[
        list[str] | None,
        Parameter(
            name="--trusted-source",
            help="Trusted local_from_python source root or glob. Repeat for multiple.",
            negative_iterable=(),
        ),
    ] = None,
    trusted_hydration: Annotated[
        bool | None,
        Parameter(
            name="--trusted-hydration",
            help="Allow all local_from_python execution during hydration for trusted inputs.",
        ),
    ] = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    submit_recovery_attempts: Annotated[
        int,
        Parameter(
            help=(
                "Number of post-failed-submit recovery lookups before resubmitting; "
                "higher values wait longer for delayed run registration."
            )
        ),
    ] = _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS,
    log_type: LogTypeOption = "console",
) -> None:
    """Hydrate and submit a local pipeline YAML file as a run."""

    specs = {
        "pipeline_path": ("pipeline_path", pipeline_path, None, False, True, optional_path),
        "arg": (arg, None),
        "args_json": (args_json, None),
        "args_config": ("args", None, None, True),
        "annotation": (annotation, None),
        "hydrate": (hydrate, True),
        "dry_run": (dry_run, None),
        "run_as": (run_as, None),
        "trusted_source": (trusted_source, None),
        "trusted_hydration_cli": ("trusted_hydration_cli", trusted_hydration, None, False),
        "submit_recovery_attempts": (submit_recovery_attempts, _DEFAULT_SUBMIT_RECOVERY_ATTEMPTS),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> dict[str, Any]:
        kwargs = {
            "run_args": parse_json_or_key_values(args.args_json or args.args_config, args.arg),
            "annotations": parse_key_value_entries(args.annotation),
            "hydrate": bool(args.hydrate),
            "run_as": args.run_as,
        }
        if args.dry_run:
            return manager.build_submit_body(args.pipeline_path, **kwargs)
        result = manager.run_pipeline(
            args.pipeline_path,
            **kwargs,
            submit_recovery_attempts=args.submit_recovery_attempts,
        )
        return result["response"]

    _run_manager_action(config, base_url, specs, action)


@app.command(name="details")
def pipeline_runs_details(
    run_id: str | None = None,
    *,
    execution_id: str | None = None,
    include_implementations: bool | None = None,
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
        "execution_id": (execution_id, None),
        "include_implementations": (include_implementations, None),
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
            include_implementations=bool(args.include_implementations),
            execution_id=args.execution_id,
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
    exit_on_first_failure: bool = False,
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
        "exit_on_first_failure": (exit_on_first_failure, False),
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
            exit_on_first_failure=bool(args.exit_on_first_failure),
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
    name: str | None = None,
    created_by: str | None = None,
    annotation: Annotated[
        list[str] | None,
        Parameter(help="Annotation filter as key or key=value. Repeat for multiple.", negative_iterable=()),
    ] = None,
    annotations_json: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    local_time: bool | None = None,
    raw_query: Annotated[
        str | None,
        Parameter(name="--query", help="Raw filter_query JSON, plain or URL-encoded."),
    ] = None,
    limit: int | None = None,
    page_token: str | None = None,
    include_pipeline_names: bool | None = None,
    include_execution_stats: bool | None = None,
    output: str | None = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Search/list pipeline runs using simple or rich Tangle API filters."""
    specs = {
        "query": ("filter", query, None, False),
        "filter_query": (filter_query, None),
        "name": (name, None),
        "created_by": (created_by, None),
        "annotation": (annotation, None),
        "annotations_json": (annotations_json, None),
        "start_date": (start_date, None),
        "end_date": (end_date, None),
        "local_time": (local_time, None),
        "raw_query": (raw_query, None),
        "limit": (limit, None),
        "page_token": (page_token, None),
        "include_pipeline_names": (include_pipeline_names, None),
        "include_execution_stats": (include_execution_stats, None),
        "output": (output, "json"),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> object:
        rich_search = any(
            getattr(args, attr)
            for attr in (
                "name",
                "created_by",
                "annotation",
                "annotations_json",
                "start_date",
                "end_date",
                "raw_query",
                "limit",
            )
        )
        if not rich_search:
            return manager.search_runs(
                filter=args.query,
                filter_query=args.filter_query,
                page_token=args.page_token,
                include_pipeline_names=args.include_pipeline_names,
                include_execution_stats=args.include_execution_stats,
            )

        annotations: dict[str, str | None] | None = None
        if args.annotation:
            annotations = {}
            for item in args.annotation:
                key, value = parse_annotation(str(item))
                annotations[key] = value
        if args.annotations_json:
            loaded = json.loads(args.annotations_json)
            if not isinstance(loaded, dict):
                raise PipelineRunError("--annotations-json must be a JSON object")
            annotations = annotations or {}
            annotations.update({str(key): value for key, value in loaded.items()})
        parsed_query = normalize_query_input(args.raw_query) if args.raw_query else None
        result = manager.search_pipeline_runs(
            name=args.name,
            created_by=args.created_by,
            annotations=annotations,
            start_date=args.start_date,
            end_date=args.end_date,
            local_time=bool(args.local_time),
            query=parsed_query,
            limit=int(args.limit or 10),
            page_token=args.page_token,
        )
        if "error" in result:
            raise PipelineRunError(str(result["error"]))
        if str(args.output or "json").lower() == "table":
            print(result.get("cli_table", ""))
            return None
        return result

    _run_manager_action(config, base_url, specs, action)


@app.command(name="export")
def pipeline_runs_export(
    run_id: str | None = None,
    *,
    output: pathlib.Path | None = None,
    dehydrate: Annotated[
        bool | None,
        Parameter(help="Dehydrate exported pipeline specs into portable component refs."),
    ] = None,
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
        "dehydrate": (dehydrate, None),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }

    def action(manager: PipelineRunManager, args: ArgsContainer) -> object:
        result = manager.export_run(args.run_id, args.output, dehydrate=bool(args.dehydrate))
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
    _run_annotation_action(config, base_url, specs, lambda manager, args: manager.list_annotations(args.run_id))


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
        "value": (value, None),
        "log_type": (log_type, "console"),
        **api_arg_specs(base_url=base_url, token=token, auth_header=auth_header, header=header),
    }
    _run_annotation_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.set_annotation(args.run_id, args.key, args.value),
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
    _run_annotation_action(
        config,
        base_url,
        specs,
        lambda manager, args: manager.delete_annotation(args.run_id, args.key),
    )
