"""Programmatic dynamic OpenAPI client for Tangle backends."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from .api_schema import (
    OperationCommand,
    fetch_schema,
    load_cached_schema,
    operation_aliases,
    operation_map,
    refresh_schema,
    resolve_operation,
)
from .api_transport import (
    DEFAULT_TIMEOUT_SECONDS,
    _normalize_base_url,
    default_base_url,
    request_operation,
)


class TangleOpenApiClient:
    """Dynamic client generated from a Tangle OpenAPI schema.

    The client intentionally reuses the same schema cache, operation naming,
    parameter mapping, URL construction, and auth/header handling as
    ``tangle api ...``. No network or cache work happens at import time; choose
    one of the ``from_*`` constructors to provide or load a schema.
    """

    def __init__(
        self,
        schema: dict[str, Any],
        *,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.schema = schema
        self.base_url = _normalize_base_url(base_url or default_base_url())
        self.headers = dict(headers or {})
        self.token = token
        self.auth_header = auth_header
        self.header = _header_list(header)
        self.timeout = timeout
        self._operations = operation_map(schema)
        self._aliases = self._build_alias_map(self._operations)
        self._groups = self._build_groups(self._operations)

    @classmethod
    def from_schema(
        cls,
        schema: dict[str, Any],
        *,
        base_url: str | None = None,
        headers: dict[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> TangleOpenApiClient:
        """Create a client from an already loaded OpenAPI schema."""

        return cls(
            schema,
            base_url=base_url,
            headers=headers,
            token=token,
            auth_header=auth_header,
            header=header,
            timeout=timeout,
        )

    @classmethod
    def from_cache(
        cls,
        base_url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> TangleOpenApiClient:
        """Create a client from the local schema cache without network access."""

        normalized_base_url = _normalize_base_url(base_url or default_base_url())
        schema = load_cached_schema(normalized_base_url)
        if schema is None:
            raise FileNotFoundError(
                f"No cached OpenAPI schema for {normalized_base_url}; "
                "call TangleOpenApiClient.from_cache_or_refresh(...) or run `tangle api refresh`."
            )
        return cls.from_schema(
            schema,
            base_url=normalized_base_url,
            headers=headers,
            token=token,
            auth_header=auth_header,
            header=header,
            timeout=timeout,
        )

    @classmethod
    def from_url(
        cls,
        base_url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> TangleOpenApiClient:
        """Fetch ``/openapi.json`` and create a client without writing the cache."""

        normalized_base_url = _normalize_base_url(base_url or default_base_url())
        schema = fetch_schema(
            normalized_base_url,
            token=token,
            header=header,
            auth_header=auth_header,
            headers=headers,
        )
        return cls.from_schema(
            schema,
            base_url=normalized_base_url,
            headers=headers,
            token=token,
            auth_header=auth_header,
            header=header,
            timeout=timeout,
        )

    @classmethod
    def from_cache_or_refresh(
        cls,
        base_url: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        token: str | None = None,
        auth_header: str | None = None,
        header: list[str] | str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> TangleOpenApiClient:
        """Create a client from cache, fetching and caching the schema on miss."""

        normalized_base_url = _normalize_base_url(base_url or default_base_url())
        schema = load_cached_schema(normalized_base_url)
        if schema is None:
            schema, _ = refresh_schema(
                normalized_base_url,
                token=token,
                header=header,
                auth_header=auth_header,
                headers=headers,
            )
        return cls.from_schema(
            schema,
            base_url=normalized_base_url,
            headers=headers,
            token=token,
            auth_header=auth_header,
            header=header,
            timeout=timeout,
        )

    @property
    def operations(self) -> tuple[str, ...]:
        """Canonical operation names exposed by this schema."""

        return tuple(sorted(self._operations))

    def request(self, operation_name: str, **params: Any) -> httpx.Response:
        """Perform an operation and return the raw ``httpx.Response``.

        Operation parameters are passed as keyword arguments. Per-call overrides
        for ``base_url``, ``token``, ``auth_header``, ``header``, ``headers``,
        ``body``, and ``timeout`` are also supported.
        """

        operation = self._resolve(operation_name)
        base_url = params.pop("base_url", self.base_url)
        token = params.pop("token", self.token)
        auth_header = params.pop("auth_header", self.auth_header)
        header_override = params.pop("header", None)
        header = self.header + _header_list(header_override)
        headers_override = params.pop("headers", None)
        headers = {**self.headers, **dict(headers_override or {})}
        body = params.pop("body", None)
        timeout = params.pop("timeout", self.timeout)
        return request_operation(
            operation,
            params,
            base_url=base_url,
            token=token,
            auth_header=auth_header,
            header_entries=header,
            headers=headers,
            body=body,
            timeout=timeout,
        )

    def call(self, operation_name: str, **params: Any) -> Any:
        """Perform an operation and decode the response body.

        JSON responses return decoded JSON; text responses return ``str``;
        non-text responses return ``bytes``; empty responses return ``None``.
        """

        return decode_response(self.request(operation_name, **params))

    def __getattr__(self, name: str) -> _OperationGroup:
        canonical_group = self._groups.get(name) or self._groups.get(name.replace("_", "-"))
        if canonical_group is None:
            raise AttributeError(name)
        return _OperationGroup(self, canonical_group)

    def _resolve(self, operation_name: str) -> OperationCommand:
        return self._aliases.get(operation_name) or resolve_operation(self._operations, operation_name)

    @staticmethod
    def _build_alias_map(
        operations: dict[str, OperationCommand]
    ) -> dict[str, OperationCommand]:
        aliases: dict[str, OperationCommand] = {}
        for name, operation in operations.items():
            for alias in operation_aliases(name):
                aliases.setdefault(alias, operation)
        return aliases

    @staticmethod
    def _build_groups(
        operations: dict[str, OperationCommand]
    ) -> dict[str, str]:
        groups: dict[str, str] = {}
        for operation in operations.values():
            groups.setdefault(operation.group_name, operation.group_name)
            groups.setdefault(operation.group_name.replace("-", "_"), operation.group_name)
        return groups


class _OperationGroup:
    """Dynamic operation namespace returned by ``client.<resource>``."""

    def __init__(self, client: TangleOpenApiClient, group_name: str) -> None:
        self._client = client
        self._group_name = group_name

    def call(self, command_name: str, **params: Any) -> Any:
        return self._client.call(f"{self._group_name}.{command_name}", **params)

    def request(self, command_name: str, **params: Any) -> httpx.Response:
        return self._client.request(f"{self._group_name}.{command_name}", **params)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        operation_name = f"{self._group_name}.{name}"
        try:
            self._client._resolve(operation_name)
        except KeyError as exc:
            raise AttributeError(name) from exc

        def operation(**params: Any) -> Any:
            return self._client.call(operation_name, **params)

        operation.__name__ = name
        return operation


def _header_list(header: list[str] | str | None) -> list[str]:
    if header is None:
        return []
    if isinstance(header, str):
        return [header]
    return list(header)


def decode_response(response: httpx.Response) -> Any:
    """Decode an ``httpx.Response`` into JSON, text, bytes, or ``None``."""

    if not response.content:
        return None
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" in content_type:
        return response.json()
    if content_type.startswith("text/") or "charset=" in content_type:
        return response.text
    return response.content
