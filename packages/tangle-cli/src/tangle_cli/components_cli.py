import json
import pathlib
import sys
from typing import Annotated, Any

from cyclopts import App, Parameter

from .api_transport import DEFAULT_TIMEOUT_SECONDS
from .args_container import ArgsContainer, ConfigFileError

app = App(name="components", help="Work with Tangle component definitions.")

generate_app = App(name="generate", help="Generate component definition files.")
app.command(generate_app)

component_references_app = App(
    name="component-references", help="Work with component reference metadata."
)
app.command(component_references_app)

annotations_app = App(name="annotations", help="Work with component annotations.")
app.command(annotations_app)

ConfigOption = Annotated[
    str | None,
    Parameter(help="YAML/JSON config file providing command defaults."),
]
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


def _client_from_options(
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | None = None,
) -> Any:
    try:
        from .client import TangleApiClient
    except ModuleNotFoundError as exc:
        if exc.name == "tangle_api":
            raise SystemExit(
                "Native generated Tangle API bindings are required for component "
                "publish/deprecate commands. Install tangle-cli[native] or "
                "provide a local tangle_api.generated package."
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


def publish_component(*args: Any, **kwargs: Any) -> Any:
    from .component_publisher import publish_component as _publish_component

    return _publish_component(*args, **kwargs)


def deprecate_component(*args: Any, **kwargs: Any) -> Any:
    from .component_publisher import deprecate_component as _deprecate_component

    return _deprecate_component(*args, **kwargs)


# region components


@app.command(name="validate")
def components_validate(component_path: str):
    raise NotImplementedError()


@app.command(name="set-container-image")
def components_set_container_image(component_path: str):
    raise NotImplementedError()


# endregion


# region components/annotations


def _missing_required_args(command_name: str, provided: dict[str, object]) -> None:
    """Print help for truly empty commands, but error on partial invocations."""

    if all(value is None for value in provided.values()):
        annotations_app.help_print([command_name])
        raise SystemExit(0)

    missing = [name for name, value in provided.items() if value is None]
    print(f"Missing required argument(s): {', '.join(missing)}", file=sys.stderr)
    raise SystemExit(1)


@annotations_app.command(name="set")
def components_annotations_set(
    component_path: str | None = None,
    key: str | None = None,
    value: str | None = None,
    output_component_path: str | None = None,
):
    """Sets annotation value in component file."""
    if component_path is None or key is None or value is None:
        _missing_required_args(
            "set",
            {"component_path": component_path, "key": key, "value": value},
        )
    raise NotImplementedError()


@annotations_app.command(name="get")
def components_annotations_get(
    component_path: str | None = None, keys: list[str] | None = None
):
    """Gets annotation values from component file."""
    if component_path is None or keys is None:
        _missing_required_args("get", {"component_path": component_path, "keys": keys})
    raise NotImplementedError()


# endregion


# region components/generate


@generate_app.command(name="from-template", show=False)
def components_generate_from_template(
    template_name: str,
    output_component_path: pathlib.Path,
):
    raise NotImplementedError()


def _components_generate_from_python_impl(
    *,
    python_file: pathlib.Path | None = None,
    output: pathlib.Path | None = None,
    name: str | None = None,
    function_name: str | None = None,
    image: str | None = None,
    dependencies_from: pathlib.Path | None = None,
    strip_code: bool | None = None,
    use_legacy_naming: bool | None = None,
    mode: str | None = None,
    resolve_root: pathlib.Path | None = None,
    config: str | None = None,
) -> None:
    all_args = _load_args(
        config,
        python_file=("python_file", python_file, None, False, True, _optional_path),
        output=(output, None, _optional_path),
        name=(name, None),
        function_name=("function", function_name, None, False),
        image=(image, None),
        dependencies_from=(dependencies_from, None, _optional_path),
        strip_code=(strip_code, None),
        use_legacy_naming=(use_legacy_naming, None),
        mode=(mode, None),
        resolve_root=(resolve_root, None, _optional_path),
    )
    for args in all_args:
        from .component_generator import determine_output_path, regenerate_yaml

        selected_mode = args.mode or "inline"
        if selected_mode not in {"inline", "bundle"}:
            raise SystemExit("--mode must be 'inline' or 'bundle'")
        python_path = pathlib.Path(args.python_file)
        output_path = determine_output_path(
            python_path,
            args.output,
            output_is_dir=False,
            use_legacy_naming=bool(args.use_legacy_naming),
        )
        success = regenerate_yaml(
            python_file=python_path,
            output_path=output_path,
            function_name=args.function_name,
            custom_name=args.name,
            image=args.image,
            dependencies_from=args.dependencies_from,
            strip_code=bool(args.strip_code),
            mode=selected_mode,
            resolve_root=args.resolve_root,
            verbose=True,
        )
        if not success:
            raise SystemExit(1)


@generate_app.command(name="from-python")
def components_generate_from_python(
    python_file: pathlib.Path | None = None,
    *,
    output: pathlib.Path | None = None,
    name: str | None = None,
    function_name: Annotated[
        str | None,
        Parameter(name="--function", alias="-f", help="Function name to extract."),
    ] = None,
    image: str | None = None,
    dependencies_from: pathlib.Path | None = None,
    strip_code: bool | None = None,
    use_legacy_naming: bool | None = None,
    mode: str | None = None,
    resolve_root: pathlib.Path | None = None,
    config: ConfigOption = None,
) -> None:
    """Generate a component YAML file from a local Python function."""

    _components_generate_from_python_impl(
        python_file=python_file,
        output=output,
        name=name,
        function_name=function_name,
        image=image,
        dependencies_from=dependencies_from,
        strip_code=strip_code,
        use_legacy_naming=use_legacy_naming,
        mode=mode,
        resolve_root=resolve_root,
        config=config,
    )


@generate_app.command(name="from-python-function")
def components_generate_from_python_function(
    python_file: pathlib.Path | None = None,
    *,
    output: pathlib.Path | None = None,
    name: str | None = None,
    function_name: Annotated[
        str | None,
        Parameter(name="--function", alias="-f", help="Function name to extract."),
    ] = None,
    image: str | None = None,
    dependencies_from: pathlib.Path | None = None,
    strip_code: bool | None = None,
    use_legacy_naming: bool | None = None,
    mode: str | None = None,
    resolve_root: pathlib.Path | None = None,
    config: ConfigOption = None,
) -> None:
    """Compatibility alias for `generate from-python`."""

    _components_generate_from_python_impl(
        python_file=python_file,
        output=output,
        name=name,
        function_name=function_name,
        image=image,
        dependencies_from=dependencies_from,
        strip_code=strip_code,
        use_legacy_naming=use_legacy_naming,
        mode=mode,
        resolve_root=resolve_root,
        config=config,
    )


# endregion


@app.command(name="bump-version")
def components_bump_version(
    yaml_file: pathlib.Path | None = None,
    *,
    set_version: str | None = None,
    update_timestamp: bool | None = None,
    config: ConfigOption = None,
) -> None:
    """Bump version metadata in a component YAML file."""

    all_args = _load_args(
        config,
        yaml_file=("yaml_file", yaml_file, None, False, True, _optional_path),
        set_version=(set_version, None),
        update_timestamp=(update_timestamp, None),
    )
    result: dict[str, Any] = {}
    from .version_manager import bump_version

    for args in all_args:
        result = bump_version(
            args.yaml_file,
            set_version=args.set_version,
            update_timestamp=bool(args.update_timestamp),
        )
        if result.get("status") != "success":
            raise SystemExit(1)
    if result:
        print(result)


@app.command(name="publish")
def components_publish(
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
    base_url: BaseUrlOption = None,
    token: TokenOption = None,
    auth_header: AuthHeaderOption = None,
    header: HeaderOption = None,
    config: ConfigOption = None,
) -> None:
    """Publish one component YAML file to a Tangle component registry."""

    for args in _load_args(
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
        **_api_arg_specs(
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header=header,
        ),
    ):
        client = None if args.dry_run else _client_from_options(
            base_url=args.base_url,
            token=args.token,
            auth_header=args.auth_header,
            header=args.header,
        )
        result = publish_component(
            client,
            args.component_path,
            image=args.image,
            name=args.name,
            description=args.description,
            annotations=args.annotations,
            dry_run=bool(args.dry_run),
            git_remote_sha=args.git_remote_sha,
            git_remote_branch=args.git_remote_branch,
            git_remote_url=args.git_remote_url,
            git_root=args.git_root,
        )
        result_dict = result.to_dict() if hasattr(result, "to_dict") else result
        _print_json(result_dict)
        if isinstance(result_dict, dict) and result_dict.get("status") == "failed":
            raise SystemExit(1)


@app.command(name="deprecate")
def components_deprecate(
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
        if isinstance(result_dict, dict) and result_dict.get("status") == "failed":
            raise SystemExit(1)
