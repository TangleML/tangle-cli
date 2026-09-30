"""`tangle sdk pipelines` local pipeline commands."""

from __future__ import annotations

import pathlib
from typing import Annotated, Any, Literal

from cyclopts import App, Parameter

from .cli_helpers import (
    LazyTangleApiClient,
    load_config_or_exit,
    optional_path,
    parse_image_overrides,
    parse_overrides,
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
from .pipeline_decompiler import DecompileError, decompile_pipeline_file
from .pipelines import (
    PipelineValidationError,
    compile_pipeline_file,
    dehydrate_pipeline_file,
    generate_mermaid,
    hydrate_pipeline_file,
    layout_pipeline_file,
    validate_pipeline_file,
)

app = App(
    name="pipelines",
    help="Validate and visualize local Tangle pipeline specs.",
)


@app.command(name="validate")
def pipelines_validate(
    pipeline_path: pathlib.Path,
    *,
    log_type: LogTypeOption = "console",
) -> None:
    """Validate a local pipeline YAML file."""

    logger, finalize_logs = logger_for_log_type(log_type)
    try:
        try:
            validate_pipeline_file(pipeline_path)
        except PipelineValidationError as exc:
            raise SystemExit(str(exc)) from exc
        print(f"Valid pipeline: {pipeline_path}")
    finally:
        finalize_logs()


@app.command(name="diagram")
def pipelines_diagram(
    pipeline_path: pathlib.Path,
    *,
    log_type: LogTypeOption = "console",
) -> None:
    """Print a Mermaid dependency diagram for a local pipeline YAML file."""

    logger, finalize_logs = logger_for_log_type(log_type)
    try:
        try:
            pipeline = validate_pipeline_file(pipeline_path)
        except PipelineValidationError as exc:
            raise SystemExit(str(exc)) from exc
        print(generate_mermaid(pipeline))
    finally:
        finalize_logs()


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _header_entries(cli_header: list[str] | None, config: dict[str, object]) -> list[str] | None:
    if cli_header is not None:
        return cli_header
    config_header = config.get("header")
    if isinstance(config_header, list):
        return [str(entry) for entry in config_header]
    if isinstance(config_header, str):
        return [config_header]
    return None


def _trusted_hydration_config(config: dict[str, object]) -> dict[str, Any]:
    trusted = config.get("trusted_hydration", {})
    return trusted if isinstance(trusted, dict) else {}


def _trusted_sources(
    cli_sources: list[str] | None,
    config: dict[str, object],
) -> list[str]:
    sources: list[str] = []
    config_sources = _trusted_hydration_config(config).get("trusted_python_sources", [])
    if isinstance(config_sources, str):
        sources.append(config_sources)
    elif isinstance(config_sources, list):
        sources.extend(str(source) for source in config_sources)
    if cli_sources:
        sources.extend(cli_sources)
    return [source for source in sources if source]


def _allow_all_hydration(
    trusted_hydration: bool | None,
    config: dict[str, object],
) -> bool:
    return bool(trusted_hydration or _trusted_hydration_config(config).get("allow_all", False))


def _parse_vars(values: list[str] | dict[str, object] | None) -> dict[str, str]:
    parsed: dict[str, str] = {}
    if isinstance(values, dict):
        return {str(key): str(value) for key, value in values.items()}
    for value in values or []:
        if "=" not in value:
            raise SystemExit("--var entries must use KEY=VALUE syntax")
        key, parsed_value = value.split("=", 1)
        if not key:
            raise SystemExit("--var entries must use KEY=VALUE syntax")
        parsed[key] = parsed_value
    return parsed


@app.command(name="hydrate")
def pipelines_hydrate(
    pipeline_path: pathlib.Path,
    *,
    output: Annotated[
        pathlib.Path | None,
        Parameter(
            name="--output",
            alias="-o",
            help="Output path. Defaults to printing hydrated YAML to stdout.",
        ),
    ] = None,
    var: Annotated[
        list[str] | None,
        Parameter(
            name="--var",
            help="Template override as KEY=VALUE. Repeat for multiple overrides.",
            negative_iterable=(),
        ),
    ] = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
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
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Hydrate a local pipeline YAML file."""

    config_values = load_config_or_exit(config)
    config_base_url = _optional_str(config_values.get("base_url"))
    resolved_base_url = base_url if base_url is not None else config_base_url
    include_env_credentials = not (base_url is None and config_base_url is not None)
    resolved_var = var if var is not None else config_values.get("var")
    resolved_token = token
    if resolved_token is None:
        resolved_token = _optional_str(config_values.get("token"))

    logger, finalize_logs = logger_for_log_type(log_type)
    try:
        result = hydrate_pipeline_file(
            pipeline_path,
            output=output or optional_path(config_values.get("output")),
            overrides=_parse_vars(resolved_var),
            base_url=resolved_base_url,
            token=resolved_token,
            auth_header=(
                auth_header
                if auth_header is not None
                else _optional_str(config_values.get("auth_header"))
            ),
            header=_header_entries(header, config_values),
            include_env_credentials=include_env_credentials,
            logger=logger,
            trusted_python_sources=_trusted_sources(trusted_source, config_values),
            allow_all_hydration=_allow_all_hydration(trusted_hydration, config_values),
            client=LazyTangleApiClient(
                command_name="pipeline hydration with API-backed component references",
                base_url=resolved_base_url,
                token=resolved_token,
                auth_header=(
                    auth_header
                    if auth_header is not None
                    else _optional_str(config_values.get("auth_header"))
                ),
                header=_header_entries(header, config_values),
                include_env_credentials=include_env_credentials,
                logger=logger,
            ),
        )
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        finalize_logs()

    if result.output_path is None:
        print(result.content, end="" if result.content.endswith("\n") else "\n")
    else:
        print(
            f"Hydrated {pipeline_path} -> {result.output_path} "
            f"({result.resolved_components} component(s) resolved)."
        )


@app.command(name="dehydrate")
def pipelines_dehydrate(
    # ``str``, not ``Path``: Path("gs://b/x") collapses to the LOCAL path
    # gs:/b/x, which would hide a URI from the local-path-only check.
    pipeline_path: str,
    *,
    output: Annotated[
        str | None,
        Parameter(
            name="--output",
            alias="-o",
            help="Local output path for the dehydrated pipeline YAML (required).",
        ),
    ] = None,
    mode: Annotated[
        Literal["auto", "digest", "name", "url", "file"] | None,
        Parameter(
            name="--mode",
            help=(
                "How to replace inline component specs. auto: canonical URL, else a "
                "verified published digest, else a local file. digest/name: a "
                "verified digest or owner-pinned name with a local fallback, via a "
                "<output stem>.components.yaml resolve config. url: canonical URL. "
                "file: always a local file. Default: auto."
            ),
        ),
    ] = None,
    components_dir: Annotated[
        str | None,
        Parameter(
            name="--components-dir",
            help="Where extracted component files go. Defaults to components/ beside the output.",
        ),
    ] = None,
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Dehydrate inline component specs in a local pipeline YAML file."""

    config_values = load_config_or_exit(config)
    resolved_output = output or _optional_str(config_values.get("output"))
    if resolved_output is None:
        raise SystemExit("--output is required: dehydration writes files beside the output.")
    config_base_url = _optional_str(config_values.get("base_url"))
    resolved_base_url = base_url if base_url is not None else config_base_url
    include_env_credentials = not (base_url is None and config_base_url is not None)
    resolved_token = token if token is not None else _optional_str(config_values.get("token"))
    resolved_auth_header = (
        auth_header if auth_header is not None else _optional_str(config_values.get("auth_header"))
    )
    resolved_mode = mode if mode is not None else (_optional_str(config_values.get("mode")) or "auto")

    logger, finalize_logs = logger_for_log_type(log_type)
    try:
        result = dehydrate_pipeline_file(
            pipeline_path,
            output=resolved_output,
            mode=resolved_mode,
            components_dir=components_dir or _optional_str(config_values.get("components_dir")),
            base_url=resolved_base_url,
            logger=logger,
            # Lazy: a network or auth failure during verification degrades to a
            # local copy, while a malformed --header/--auth-header still exits.
            client=LazyTangleApiClient(
                command_name="pipeline dehydration with published-component verification",
                base_url=resolved_base_url,
                token=resolved_token,
                auth_header=resolved_auth_header,
                header=_header_entries(header, config_values),
                include_env_credentials=include_env_credentials,
                logger=logger,
            ),
        )
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        finalize_logs()

    print(f"Dehydrated {pipeline_path} -> {result.output_path} (mode: {result.mode}).")
    if result.resolve_config is not None:
        print(f"  Resolve config: {result.resolve_config}")
    if result.components_dir is not None:
        print(f"  Components: {result.extracted_components} file(s) in {result.components_dir}")


@app.command(name="compile")
def pipelines_compile(
    pipeline_path: pathlib.Path,
    *,
    output: Annotated[
        pathlib.Path,
        Parameter(
            name="--output",
            alias="-o",
            help="Output path for the compiled dehydrated pipeline YAML.",
        ),
    ],
    pipeline: Annotated[
        str | None,
        Parameter(
            name="--pipeline",
            help=(
                "Select the root @pipeline function by name when the file "
                "defines several."
            ),
        ),
    ] = None,
    override: Annotated[
        list[str] | None,
        Parameter(
            name="--override",
            help="Compile-time config override as KEY=VALUE. Repeat for multiple.",
            negative_iterable=(),
        ),
    ] = None,
    image: Annotated[
        list[str] | None,
        Parameter(
            name="--image",
            help=(
                "Compile-time image-id override as ID=REF for @task(image_id=ID). "
                "Repeat for multiple IDs."
            ),
            negative_iterable=(),
        ),
    ] = None,
    log_type: LogTypeOption = "console",
) -> None:
    """Compile a Python-authored pipeline to a dehydrated YAML bundle."""

    logger, finalize_logs = logger_for_log_type(log_type)
    try:
        result = compile_pipeline_file(
            pipeline_path,
            output,
            overrides=parse_overrides(override),
            image_overrides=parse_image_overrides(image),
            pipeline_name=pipeline,
            logger=logger,
        )
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        finalize_logs()

    print(
        f"Compiled {pipeline_path} -> {result.pipeline_path} "
        f"({result.task_count} task(s))."
    )
    if result.components_path is not None:
        print(f"Wrote component sidecar: {result.components_path}")
    for subgraph_path in result.subgraph_paths:
        print(f"Wrote subgraph: {subgraph_path}")
    for warning in result.warnings:
        print(f"warning: {warning}")


@app.command(name="decompile")
def pipelines_decompile(
    pipeline_path: pathlib.Path,
    *,
    output: Annotated[
        pathlib.Path,
        Parameter(
            name="--output",
            alias="-o",
            help=(
                "Output path for the generated Python pipeline source. "
                "Always replaced, atomically."
            ),
        ),
    ],
    verify: Annotated[
        bool,
        Parameter(
            name="--verify",
            help=(
                "Recompile the generated source and require semantic "
                "equality with the input. Disabling this removes the only "
                "proof the output is faithful."
            ),
        ),
    ] = True,
    python_tasks: Annotated[
        bool,
        Parameter(
            name="--python-tasks",
            help=(
                "Write components the Python component generator made, whose "
                "source is recorded in full, as @task functions in "
                "<stem>_tasks/. The source is copied, never run; compiling the "
                "script later imports it. --no-python-tasks keeps every "
                "component a digest-pinned YAML leaf."
            ),
        ),
    ] = True,
    local_fallbacks: Annotated[
        bool,
        Parameter(
            name="--local-fallbacks",
            negative="",
            help=(
                "Also keep a local copy of every component the library "
                "verified, behind its published digest, so the output "
                "compiles and hydrates with the library unreachable. By "
                "default those components are referenced by digest alone."
            ),
        ),
    ] = False,
) -> None:
    """Generate Python pipeline source from a pipeline YAML.

    Accepts hydrated or dehydrated YAML. Components are always resolved and
    re-pinned before code generation.
    """

    try:
        result = decompile_pipeline_file(
            pipeline_path, output, verify=verify, python_tasks=python_tasks, include_local_fallbacks=local_fallbacks
        )
    except DecompileError as exc:
        raise SystemExit(str(exc)) from exc

    print(
        f"Decompiled {pipeline_path} -> {result.output_path} "
        f"({result.task_count} task(s))."
    )
    if result.subgraph_module is not None:
        print(f"Nested graphs are @pipeline functions in {result.subgraph_module}; keep it beside the script.")
    if result.resolve_config is not None:
        print(
            f"Components resolve through {result.resolve_config}, with "
            f"{len(result.component_files)} local copy(ies) in "
            f"{result.components_dir}. Keep both beside the script: the "
            "compiler resolves relative component URLs against the compiled "
            "YAML's directory, so compile the script in place."
        )
    if result.digest_refs:
        print(
            f"{result.digest_refs} component(s) are referenced by their published digest "
            "alone: hydrating the output needs the component library. Pass "
            "--local-fallbacks to also keep local copies for offline use."
        )
    if result.tasks_package is not None:
        print(
            f"{result.python_tasks} Python component(s) are @task functions in "
            f"{result.tasks_package}, holding source recovered verbatim from the "
            "input. It was not run here: `compile` imports it, and hydrate/submit/"
            "run execute it to rebuild each component (from the current "
            "directory tree or a --trusted-source), so review it first. Those "
            "components are no longer digest-pinned, and their rebuilt specs "
            "carry new provenance annotations."
        )
    if result.image_id_tasks:
        print(
            f"{result.image_id_tasks} of them set @task(image_id=...): compile resolves it "
            "in your environment (--image ID=REF, then registered defaults) and may "
            "pick a different image than the input recorded. Use --no-python-tasks "
            "for the digest-exact components."
        )
    if result.unwrap_tasks:
        print(
            f"{result.unwrap_tasks} of them use @task(unwrap=...): the flattened inputs' "
            "names were checked, their types are derived again at compile."
        )
    kept = result.python_candidates - result.python_tasks
    if kept > 0:
        print(f"{kept} component(s) with recorded Python source stayed YAML leaves.")
    if result.verified:
        print(
            "Verified: recompiling the output reproduces the input's "
            "semantic digest. This is not byte identity \u2014 comments, key "
            "order and task order are not recoverable."
            + (
                " For the Python components this proves the pipeline around "
                "them, not the components Tangle will rebuild from source."
                if result.tasks_package is not None
                else ""
            )
        )
    else:
        print("warning: --no-verify was passed; the output is unproven.")
    for note in result.normalizations:
        print(f"normalization applied when comparing: {note}")


@app.command(name="layout")
def pipelines_layout(
    pipeline_path: pathlib.Path,
    *,
    output: Annotated[
        pathlib.Path | None,
        Parameter(
            name="--output",
            alias="-o",
            help="Output path. Defaults to overwriting the input file.",
        ),
    ] = None,
    recursive: Annotated[
        bool | None,
        Parameter(help="Also layout nested graph component specs."),
    ] = None,
    x_spacing: Annotated[
        int,
        Parameter(help="Horizontal spacing between dependency layers."),
    ] = 300,
    y_spacing: Annotated[
        int,
        Parameter(help="Vertical spacing between tasks in the same layer."),
    ] = 120,
    log_type: LogTypeOption = "console",
) -> None:
    """Add or update editor.position annotations in a local pipeline YAML file."""

    logger, finalize_logs = logger_for_log_type(log_type)
    try:
        result = layout_pipeline_file(
            pipeline_path,
            output=output,
            recursive=bool(recursive),
            x_spacing=x_spacing,
            y_spacing=y_spacing,
        )
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        finalize_logs()
    print(
        f"Positioned {result.tasks_positioned} task(s) across "
        f"{result.graphs_positioned} graph(s)."
    )
    print(f"Wrote layout to: {result.output_path}")
