"""Shared base classes for Tangle CLI service handlers."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .api_transport import default_base_url
from .logger import Logger, get_default_logger


def _client_base_url_without_materializing(client: Any | None) -> Any | None:
    """Return a client's configured base URL without triggering lazy proxies."""

    if client is None:
        return None

    try:
        client_vars = object.__getattribute__(client, "__dict__")
    except AttributeError:
        client_vars = {}
    if isinstance(client_vars, Mapping):
        if client_vars.get("base_url"):
            return client_vars["base_url"]
        client_kwargs = client_vars.get("client_kwargs")
        if isinstance(client_kwargs, Mapping):
            return client_kwargs.get("base_url")

    try:
        return object.__getattribute__(client, "base_url")
    except AttributeError:
        return None


class TangleCliHandler:
    """Base class for CLI/services that use logging and lazy Tangle API clients."""

    _required_client_error_type: type[Exception] = RuntimeError
    _required_client_error_message = "Failed to create TangleApiClient"

    def __init__(
        self,
        *,
        dry_run: bool = False,
        client: Any = None,
        client_factory: Callable[[], Any] | None = None,
        logger: Logger | None = None,
        base_url: str | None = None,
    ) -> None:
        self.dry_run = dry_run
        client_base_url = _client_base_url_without_materializing(client)
        self.base_url = str(base_url or client_base_url or default_base_url())
        self.client = client
        self._client = client
        self._client_factory = client_factory
        self.log = logger or get_default_logger()

    def _create_client(self) -> Any | None:
        """Create the default OSS Tangle API client."""

        try:
            from .client import TangleApiClient
        except ModuleNotFoundError as exc:
            if exc.name == "tangle_api":
                self.log.error(
                    "❌ Native generated Tangle API bindings are required for Tangle API operations. "
                    "Install the default tangle-cli package with tangle-api, run from a project where local src/tangle_api shadows site-packages, or install a compatible custom tangle-api package."
                )
                return None
            raise
        return TangleApiClient(logger=self.log)

    def _set_client(self, client: Any | None) -> Any | None:
        self.client = client
        self._client = client
        client_base_url = _client_base_url_without_materializing(client)
        if client_base_url:
            self.base_url = str(client_base_url)
        return client

    def _get_client(self) -> Any | None:
        """Get or lazily create a Tangle API client instance."""

        if self._client is None and not self.dry_run:
            if self._client_factory is not None:
                return self._set_client(self._client_factory())
            return self._set_client(self._create_client())
        return self._client

    def _require_client(self) -> Any:
        """Return a Tangle API client, raising if one cannot be created."""

        client = self._get_client()
        if client is None:
            raise self._required_client_error_type(self._required_client_error_message)
        return client
