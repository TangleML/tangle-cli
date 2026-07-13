"""Shared helpers for Tangle CLI command modules."""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import requests

from .api_transport import _content_to_text, _redact_url
from .args_container import ArgsContainer, ConfigFileError

_HTTP_ERROR_BODY_LIMIT = 2000


def format_http_error(exc: requests.HTTPError) -> str:
    """Render an HTTP status failure as a concise CLI message for SDK commands.

    SDK/static client calls raise ``requests.HTTPError`` for non-2xx responses
    (via ``raise_for_status``). Client-internal helpers handle the statuses they
    can recover from (e.g. the 404 run-id -> execution-id fallback) and re-raise
    the rest, so the command layer surfaces the remaining errors here instead of
    letting a raw traceback reach the interpreter. The response status, reason,
    attempted method/URL, and body are preserved as that context is what a caller
    needs to act on. The attempted URL is stripped of userinfo and credential
    query parameters, and the response body is passed through the shared
    sensitive-key redaction before it is collapsed to a single line and
    truncated, so neither the URL nor the body can leak secrets into the
    message. Only HTTP status failures are formatted here; connection/timeout
    errors carry no response and propagate unchanged.
    """

    response = exc.response
    if response is None:
        return f"Tangle API request failed: {exc}"
    request = response.request
    if request is not None and request.url:
        target = f"{request.method} {_redact_url(request.url)}"
    else:
        target = _redact_url(response.url) or "Tangle API"
    reason = f" {response.reason}" if response.reason else ""
    summary = f"Tangle API request failed ({response.status_code}{reason}) for {target}"
    body = " ".join(_content_to_text(response.content).split())
    if not body or body == "<empty>":
        return summary
    if len(body) > _HTTP_ERROR_BODY_LIMIT:
        body = f"{body[:_HTTP_ERROR_BODY_LIMIT]}... (truncated)"
    return f"{summary}: {body}"


@contextmanager
def surface_http_errors(error_type: type[Exception]) -> Iterator[None]:
    """Re-raise ``requests.HTTPError`` as ``error_type`` with a formatted message.

    Client-internal recovery runs first and re-raises only the statuses it
    cannot handle, so errors reaching here are the ones the command layer must
    surface as a clean nonzero exit rather than a raw traceback.
    """

    try:
        yield
    except requests.HTTPError as exc:
        raise error_type(format_http_error(exc)) from exc


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
