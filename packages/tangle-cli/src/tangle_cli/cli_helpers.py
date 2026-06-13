"""Shared helpers for Tangle CLI command modules."""

from __future__ import annotations

import json
import pathlib
from typing import Any

from .args_container import ArgsContainer, ConfigFileError


def load_args_or_exit(config: str | None, **kwargs: Any) -> list[ArgsContainer]:
    """Load ArgsContainer values from CLI/config specs, exiting with CLI errors."""

    try:
        return ArgsContainer.load(config, **kwargs)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc


def print_json(payload: object) -> None:
    """Print a stable pretty JSON payload for CLI output."""

    print(json.dumps(payload, indent=2, sort_keys=True))


def load_config_or_exit(config: str | None) -> dict[str, object]:
    """Load the first YAML/JSON config mapping for commands with custom merging."""

    if config is None:
        return {}
    try:
        configs = ArgsContainer._load_config_file(config)
    except ConfigFileError as exc:
        raise SystemExit(f"Config error: {exc}") from exc
    return configs[0] if configs else {}


def optional_path(value: str | pathlib.Path | object | None) -> pathlib.Path | None:
    """Convert a CLI/config path value to Path when present."""

    if isinstance(value, pathlib.Path):
        return value
    if isinstance(value, str):
        return pathlib.Path(value)
    return None


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

    Importing CLI modules must stay native-free so local-only commands can run
    without the generated ``tangle_api`` package. This proxy delays importing and
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
                        "Native generated Tangle API bindings are required for "
                        f"{self.command_name}. Install tangle-cli[native] or provide "
                        "a local tangle_api.generated package."
                    ) from exc
                raise

            kwargs = dict(self.client_kwargs)
            kwargs.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
            self._client = TangleApiClient(**kwargs)
        return self._client

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_client(), name)


def include_env_credentials_for_args(args: ArgsContainer, cli_base_url: str | None) -> bool:
    """Suppress ambient credentials when base_url came from config, not CLI.

    Explicit config/CLI token/auth/header values remain present on *args* and are
    passed through by callers. This helper only controls environment fallback.
    """

    config_base_url = getattr(args, "_config", {}).get("base_url")
    return not (cli_base_url is None and config_base_url is not None)
