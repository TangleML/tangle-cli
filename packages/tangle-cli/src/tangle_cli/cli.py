from cyclopts import App

from . import (
    __version__,
    api_cli,
    artifacts_cli,
    components_cli,
    pipeline_runs_cli,
    pipelines_cli,
    published_components_cli,
    quickstart,
    secrets_cli,
)


def version() -> None:
    """Print the installed tangle-cli package version."""

    print(__version__)


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
        version=__version__,
    )
    app.command(name="version")(version)
    app.command(quickstart.app)
    app.command(api_cli.build_app())
    app.command(build_sdk_app())
    return app


def main() -> None:
    build_app()()


if __name__ == "__main__":
    main()
