import typer

from . import api_cli
from . import components_cli

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    suggest_commands=True,
)

app.add_typer(api_cli.app, name="api")
app.add_typer(components_cli.app, name="components")


if __name__ == "__main__":
    app()
