import pathlib
import sys

from cyclopts import App

app = App(name="components", help="Work with Tangle component definitions.")

generate_app = App(name="generate", help="Generate component definition files.")
app.command(generate_app)

component_references_app = App(
    name="component-references", help="Work with component reference metadata."
)
app.command(component_references_app)

annotations_app = App(name="annotations", help="Work with component annotations.")
app.command(annotations_app)

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
    print(locals())
    raise NotImplementedError()


# endregion


# region components/generate


@generate_app.command(name="from-template", show=False)
def components_generate_from_template(
    template_name: str,
    output_component_path: pathlib.Path,
):
    raise NotImplementedError()


@generate_app.command(name="from-python-function")
def components_generate_from_python_function(output_component_path: str):
    """
    Generates component from a Python function.
    """
    raise NotImplementedError()


# endregion
