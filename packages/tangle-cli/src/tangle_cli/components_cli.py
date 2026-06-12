import pathlib
import sys
from typing import Annotated, Any

from cyclopts import App, Parameter

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


def _load_args(config: str | None, **kwargs: Any) -> list[ArgsContainer]:
    try:
        return ArgsContainer.load(config, **kwargs)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc


def _optional_path(value: str | pathlib.Path | None) -> pathlib.Path | None:
    return pathlib.Path(value) if value is not None else None



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
