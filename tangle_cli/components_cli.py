import pathlib

import typer

app = typer.Typer(no_args_is_help=True)

generate_app = typer.Typer(no_args_is_help=True)
app.add_typer(generate_app, name="generate", no_args_is_help=True)

component_references_app = typer.Typer(no_args_is_help=True)
app.add_typer(
    component_references_app, name="component-references", no_args_is_help=True
)

annotations_app = typer.Typer(no_args_is_help=True)
app.add_typer(annotations_app, name="annotations", no_args_is_help=True)

# region components


@app.command(name="validate")
def components_validate(component_path: str):
    raise NotImplementedError()


@app.command(name="set-container-image")
def components_set_container_image(component_path: str):
    raise NotImplementedError()


# endregion


# region components/annotations


@annotations_app.command(name="set", no_args_is_help=True)
def components_annotations_set(
    component_path: str, key: str, value: str, output_component_path: str | None = None
):
    """Sets annotation value in component file."""
    raise NotImplementedError()


@annotations_app.command(name="get", no_args_is_help=True)
def components_annotations_get(component_path: str, keys: list[str]):
    """Sets annotation values from component file."""
    print(locals())
    raise NotImplementedError()


# endregion


# region components/generate


@generate_app.command(name="from-template", hidden=True)
def components_generate_from_template(
    template_name: str,
    output_component_path: pathlib.Path,
):
    raise NotImplementedError()


@generate_app.command(name="from-python-function")
def components_generate_from_python_function(output_component_path: str):
    """
    Generates component from a Python function
    """
    raise NotImplementedError()


# endregion