from __future__ import annotations

import sys
from typing import Annotated

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
from .api_transport import configure_cli_verify
from .cli_helpers import dispatching
from .cli_options import CaBundleOption, VerifyTlsOption


def version() -> None:
    """Print the installed tangle-cli package version."""

    print(__version__)


def _configure_tls_from_argv(argv: list[str]) -> None:
    """Parse the global TLS flags and install the process-wide override.

    Runs before the `api` app is built so the override is in place for the
    dynamic schema discovery that happens during command construction, ahead of
    normal Cyclopts dispatch. Only tokens before the first subcommand are
    considered; flag validation and conflict detection live in
    :func:`configure_cli_verify`.
    """

    ca_bundle: str | None = None
    verify_tls: bool | None = None
    index = 1
    while index < len(argv):
        arg = argv[index]
        if arg == "--ca-bundle" and index + 1 < len(argv):
            ca_bundle = argv[index + 1]
            index += 2
            continue
        if arg.startswith("--ca-bundle="):
            ca_bundle = arg.split("=", 1)[1]
            index += 1
            continue
        if arg == "--verify-tls":
            verify_tls = True
            index += 1
            continue
        if arg == "--no-verify-tls":
            verify_tls = False
            index += 1
            continue
        break
    configure_cli_verify(ca_bundle, verify_tls)


#: Canonical program name for command identities (``tangle-cli`` is an alias).
PROGRAM_NAME = "tangle"


def _command_identity(app: App, tokens: tuple[str, ...]) -> str | None:
    """The dispatched command path, e.g. ``"tangle sdk secrets delete"``.

    Accumulated as Cyclopts resolves the command line: the program name, then
    each group, then the leaf, with options and positional arguments excluded.
    Each level is canonicalized to its first registered name, so an alias
    resolves to the real command. Selects the command's
    ``TANGLE_ROOT_CONFIG`` entry.
    """

    try:
        chain, _apps, _unused = app.parse_commands(list(tokens))
        path = [PROGRAM_NAME]
        current = app
        for token in chain:
            target = current[token]
            path.append(next(name for name in current if current[name] is target))
            current = target
    except Exception:  # an unparsable command line reports its own error later
        return None
    return " ".join(path) if chain else None


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


def build_app(argv: list[str] | None = None) -> App:
    """Build the root CLI app lazily for the current invocation.

    Global TLS flags are parsed from *argv* (defaulting to ``sys.argv``) and
    installed before the `api` app is built, because building it can trigger
    dynamic OpenAPI schema discovery over the network.
    """

    _configure_tls_from_argv(sys.argv if argv is None else argv)

    app = App(
        help="CLI for Tangle, the open-source ML pipeline orchestration platform.",
        version=__version__,
    )
    app.command(name="version")(version)
    app.command(quickstart.app)
    app.command(api_cli.build_app())
    app.command(build_sdk_app())

    @app.meta.default
    def launcher(
        *tokens: Annotated[str, Parameter(allow_leading_hyphen=True)],
        ca_bundle: CaBundleOption = None,
        verify_tls: VerifyTlsOption = None,
    ) -> None:
        """Apply global TLS options, then dispatch the requested command."""

        configure_cli_verify(ca_bundle, verify_tls)
        with dispatching(_command_identity(app, tokens)):
            app(tokens)

    return app


def main() -> None:
    build_app().meta()


if __name__ == "__main__":
    main()
