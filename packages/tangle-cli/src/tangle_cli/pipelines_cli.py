"""`tangle sdk pipelines` local pipeline commands."""

from __future__ import annotations

import pathlib
from typing import Annotated

from cyclopts import App, Parameter

from .args_container import ArgsContainer, ConfigFileError
from .pipelines import (
    PipelineValidationError,
    generate_mermaid,
    hydrate_pipeline_file,
    layout_pipeline_file,
    validate_pipeline_file,
)

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
        name="--header",
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
    name="pipelines",
    help="Validate and visualize local Tangle pipeline specs.",
)


@app.command(name="validate")
def pipelines_validate(pipeline_path: pathlib.Path) -> None:
    """Validate a local pipeline YAML file."""

    try:
        validate_pipeline_file(pipeline_path)
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Valid pipeline: {pipeline_path}")


@app.command(name="diagram")
def pipelines_diagram(pipeline_path: pathlib.Path) -> None:
    """Print a Mermaid dependency diagram for a local pipeline YAML file."""

    try:
        pipeline = validate_pipeline_file(pipeline_path)
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc
    print(generate_mermaid(pipeline))


def _load_config(config: str | None) -> dict[str, object]:
    if config is None:
        return {}
    try:
        configs = ArgsContainer._load_config_file(config)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    return configs[0] if configs else {}


def _config_value(config: dict[str, object], key: str) -> object | None:
    return config.get(key) if key in config else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_path(value: pathlib.Path | str | object | None) -> pathlib.Path | None:
    if isinstance(value, pathlib.Path):
        return value
    if isinstance(value, str):
        return pathlib.Path(value)
    return None


def _header_entries(cli_header: list[str] | None, config: dict[str, object]) -> list[str] | None:
    if cli_header is not None:
        return cli_header
    config_header = _config_value(config, "header")
    if isinstance(config_header, list):
        return [str(entry) for entry in config_header]
    if isinstance(config_header, str):
        return [config_header]
    return None


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
    config: ConfigOption = None,
) -> None:
    """Hydrate a local pipeline YAML file."""

    config_values = _load_config(config)
    config_base_url = _optional_str(_config_value(config_values, "base_url"))
    resolved_base_url = base_url if base_url is not None else config_base_url
    include_env_credentials = not (base_url is None and config_base_url is not None)
    resolved_var = var if var is not None else _config_value(config_values, "var")
    resolved_token = token
    if resolved_token is None:
        resolved_token = _optional_str(_config_value(config_values, "token"))

    try:
        result = hydrate_pipeline_file(
            pipeline_path,
            output=output or _optional_path(_config_value(config_values, "output")),
            overrides=_parse_vars(resolved_var),
            base_url=resolved_base_url,
            token=resolved_token,
            auth_header=(
                auth_header
                if auth_header is not None
                else _optional_str(_config_value(config_values, "auth_header"))
            ),
            header=_header_entries(header, config_values),
            include_env_credentials=include_env_credentials,
        )
    except PipelineValidationError as exc:
        raise SystemExit(str(exc)) from exc

    if result.output_path is None:
        print(result.content, end="" if result.content.endswith("\n") else "\n")
    else:
        print(
            f"Hydrated {pipeline_path} -> {result.output_path} "
            f"({result.resolved_components} component(s) resolved)."
        )


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
) -> None:
    """Add or update editor.position annotations in a local pipeline YAML file."""

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
    print(
        f"Positioned {result.tasks_positioned} task(s) across "
        f"{result.graphs_positioned} graph(s)."
    )
    print(f"Wrote layout to: {result.output_path}")
