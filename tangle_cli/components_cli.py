import pathlib

import cyclopts

app = cyclopts.App(name="components")

generate_app = cyclopts.App(name="generate")
app.command(generate_app)

component_references_app = cyclopts.App(name="component-references")
app.command(component_references_app)

annotations_app = cyclopts.App(name="annotations")
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


@annotations_app.command(name="set")
def components_annotations_set(
    component_path: str, key: str, value: str, output_component_path: str | None = None
):
    """Sets annotation value in component file."""
    raise NotImplementedError()


@annotations_app.command(name="get")
def components_annotations_get(component_path: str, keys: list[str]):
    """Sets annotation values from component file."""
    print(locals())
    raise NotImplementedError()


# endregion


# region components/generate

_from_template_app = cyclopts.App(name="from-template", show=False)
generate_app.command(_from_template_app)


@_from_template_app.default
def components_generate_from_template(
    template_name: str,
    output_component_path: pathlib.Path,
):
    raise NotImplementedError()


@generate_app.command(name="from-python-function")
def components_generate_from_python_function(output_component_path: str):
    """Generates component from a Python function"""
    raise NotImplementedError()


# endregion
