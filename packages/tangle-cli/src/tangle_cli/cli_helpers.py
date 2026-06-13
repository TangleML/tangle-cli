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


def include_env_credentials_for_args(args: ArgsContainer, cli_base_url: str | None) -> bool:
    """Suppress ambient credentials when base_url came from config, not CLI.

    Explicit config/CLI token/auth/header values remain present on *args* and are
    passed through by callers. This helper only controls environment fallback.
    """

    config_base_url = getattr(args, "_config", {}).get("base_url")
    return not (cli_base_url is None and config_base_url is not None)
