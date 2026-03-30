import typer

from . import api_cli

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    suggest_commands=True,
)

app.add_typer(api_cli.app, name="api")


if __name__ == "__main__":
    app()
