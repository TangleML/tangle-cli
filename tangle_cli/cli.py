import typer

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    suggest_commands=True,
)

if __name__ == "__main__":
    app()
