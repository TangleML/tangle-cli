from cyclopts import App

from . import api_cli
from . import components_cli


def build_app() -> App:
    """Build the root CLI app lazily for the current invocation."""

    app = App(
        help="CLI for Tangle, the open-source ML pipeline orchestration platform.",
        version="0.0.1",
    )
    app.command(api_cli.build_app())
    app.command(components_cli.app)
    return app


def main() -> None:
    build_app()()


if __name__ == "__main__":
    main()
