from __future__ import annotations

import sys
from typing import Annotated

import requests
from cyclopts import App, Parameter

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
from .api_transport import format_transport_error


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

    @app.meta.default
    def launcher(*tokens: Annotated[str, Parameter(allow_leading_hyphen=True)]) -> None:
        """Dispatch the requested command through the meta app.

        The runner enters the CLI via ``app.meta`` so that a sibling branch which
        registers global root options on ``app.meta.default`` (e.g. TLS flags that
        must apply before dynamic schema discovery) composes here without this
        module importing that feature: a merge keeps the richer launcher while
        this runner keeps routing through it.
        """

        app(tokens)

    return app


def run(tokens: list[str] | None = None) -> int:
    """Dispatch the CLI, rendering transport failures as one clean stderr line.

    The static requests client raises transport failures with no HTTP response;
    they are printed without a traceback and mapped to a nonzero exit. HTTP status
    errors carry a response and stay the command layer's responsibility, so they
    are re-raised unchanged.
    """

    try:
        build_app().meta(tokens)
    except requests.exceptions.RequestException as exc:
        if getattr(exc, "response", None) is not None:
            raise
        print(_transport_error_line(exc), file=sys.stderr)
        return 1
    return 0


def _transport_error_line(exc: requests.exceptions.RequestException) -> str:
    # The static client already formats its failures into a clean line, so only raw
    # requests exceptions that bypassed it need formatting here. The client module is
    # only inspected if it is already imported (it must be, to have raised the domain
    # error), so local-only commands never load the generated API bindings.
    client_module = sys.modules.get(f"{__package__}.client")
    domain_error = getattr(client_module, "TangleApiTransportError", None)
    if domain_error is not None and isinstance(exc, domain_error):
        return str(exc)
    return format_transport_error(exc)


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
