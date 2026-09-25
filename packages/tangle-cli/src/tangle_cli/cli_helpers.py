"""Shared helpers for Tangle CLI command modules."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from .args_container import ArgsContainer, ConfigFileError, load_config

# Internal plumbing only: the dispatcher records the resolved command path here
# and the helpers below hand it to ArgsContainer explicitly as ``command=``.
_DISPATCHED_COMMAND: ContextVar[str | None] = ContextVar("tangle_dispatched_command", default=None)


@contextmanager
def dispatching(command: str | None) -> Iterator[None]:
    """Record the command the CLI dispatcher resolved for the duration of a run."""

    token = _DISPATCHED_COMMAND.set(command)
    try:
        yield
    finally:
        _DISPATCHED_COMMAND.reset(token)


def dispatched_command() -> str | None:
    """The command path recorded by :func:`dispatching`, if any."""

    return _DISPATCHED_COMMAND.get()


def load_args_or_exit(config: str | None, **kwargs: Any) -> list[ArgsContainer]:
    """Load ArgsContainer values from CLI/config specs, exiting with CLI errors.

    Passes the dispatched command path to ``ArgsContainer.load(command=...)``
    so the command's ``TANGLE_ROOT_CONFIG`` entry applies.
    """

    try:
        return ArgsContainer.load(config, command=dispatched_command(), **kwargs)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc


def print_json(payload: object) -> None:
    """Print a stable pretty JSON payload for CLI output."""

    print(json.dumps(payload, indent=2, sort_keys=True))


def load_config_or_exit(config: str | None) -> dict[str, object]:
    """Load the first YAML/JSON config mapping for commands with custom merging.

    Layered over ``TANGLE_ROOT_CONFIG`` like every ``--config`` (see ``load_config``).
    """

    try:
        configs = load_config(config, command=dispatched_command())
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    return configs[0].values if configs else {}


def optional_path(value: str | pathlib.Path | object | None) -> pathlib.Path | None:
    """Convert a CLI/config path value to Path when present."""

    if isinstance(value, pathlib.Path):
        return value
    if isinstance(value, str):
        return pathlib.Path(value)
    return None


def parse_overrides(values: list[str] | None) -> dict[str, str]:
    """Parse repeatable ``--override KEY=VALUE`` compile-time cfg overrides.

    Shared verbatim by ``sdk pipelines compile`` and ``sdk pipeline-runs
    submit-from-python`` so both accept exactly the same syntax and reject the
    same mistakes. An empty VALUE is allowed (``--override note=``).
    """

    parsed: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit("--override entries must use KEY=VALUE syntax")
        key, parsed_value = value.split("=", 1)
        if not key:
            raise SystemExit("--override entries must use KEY=VALUE syntax")
        parsed[key] = parsed_value
    return parsed


def parse_image_overrides(values: list[str] | None) -> dict[str, str]:
    """Parse repeatable ``--image ID=REF`` compile-time image-id overrides."""

    parsed: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise SystemExit("--image entries must use ID=REF syntax")
        image_id, image_ref = value.split("=", 1)
        if not image_id or not image_ref:
            raise SystemExit("--image entries must use ID=REF syntax")
        parsed[image_id] = image_ref
    return parsed


def api_arg_specs(
    *,
    base_url: str | None = None,
    token: str | None = None,
    auth_header: str | None = None,
    header: list[str] | None = None,
) -> dict[str, tuple[Any, ...]]:
    """Build ArgsContainer specs for common API connection options."""

    return {
        "base_url": (base_url, None),
        "token": (token, None),
        "auth_header": (auth_header, None),
        "header": (header, None),
    }


class LazyTangleApiClient:
    """Instantiate the generated API client only when a command uses it.

    Importing CLI modules must not eagerly load generated bindings, so local-only
    commands can run without importing ``tangle_api``. This proxy delays importing and
    constructing ``TangleApiClient`` until an API method is actually accessed,
    while keeping CLI-friendly error wording in the CLI helper layer.
    """

    def __init__(self, *, command_name: str, **client_kwargs: Any) -> None:
        self.command_name = command_name
        self.client_kwargs = client_kwargs
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from .api_transport import DEFAULT_TIMEOUT_SECONDS
                from .client import TangleApiClient
            except ModuleNotFoundError as exc:
                if exc.name == "tangle_api":
                    raise SystemExit(
                        "Generated Tangle API bindings are required for "
                        f"{self.command_name}. Install the default tangle-cli package "
                        "with tangle-api, run from a project where local src/tangle_api "
                        "shadows site-packages, or install a compatible custom tangle-api package."
                    ) from exc
                raise

            kwargs = dict(self.client_kwargs)
            kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
            self._client = TangleApiClient(**kwargs)
        return self._client

    def require_available(self) -> None:
        """Materialize the client so CLI commands fail before helper imports."""

        self._get_client()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_client(), name)


def include_env_credentials_for_args(args: ArgsContainer, cli_base_url: str | None) -> bool:
    """Suppress ambient credentials when base_url came from config, not CLI.

    Explicit config/CLI token/auth/header values remain present on *args* and are
    passed through by callers. This helper only controls environment fallback.
    """

    config_base_url = getattr(args, "_config", {}).get("base_url")
    return not (cli_base_url is None and config_base_url is not None)
