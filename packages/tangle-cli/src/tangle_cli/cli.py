from cyclopts import App

from . import api_cli
from . import artifacts_cli
from . import components_cli
from . import pipeline_runs_cli
from . import pipelines_cli
from . import published_components_cli
from . import quickstart
from . import secrets_cli


def build_sdk_app() -> App:
    """Build the SDK command group."""

    sdk_app = App(
        name="sdk",
        help="Work with local Tangle SDK resources and scaffolding.",
    )
    sdk_app.command(artifacts_cli.app)
    sdk_app.command(components_cli.app)
    sdk_app.command(pipelines_cli.app)
    sdk_app.command(pipeline_runs_cli.app)
    sdk_app.command(published_components_cli.app)
    sdk_app.command(secrets_cli.app)
    return sdk_app


def build_app() -> App:
    """Build the root CLI app lazily for the current invocation."""

    app = App(
        help="CLI for Tangle, the open-source ML pipeline orchestration platform.",
        version="0.0.1",
    )
    app.command(quickstart.app)
    app.command(api_cli.build_app())
    app.command(build_sdk_app())
    return app


def main() -> None:
    build_app()()


if __name__ == "__main__":
    main()
